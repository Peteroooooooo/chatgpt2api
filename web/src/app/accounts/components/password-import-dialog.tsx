"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, KeyRound, LoaderCircle, Wand2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchKeeperJob,
  fetchLatestKeeperJob,
  fetchMailboxSettings,
  startPasswordImport,
  type PasswordImportJob,
} from "@/lib/api";

// 与后端 account_keeper.CREDENTIAL_LINE / EMAIL_ONLY_LINE 一致，只用于提示识别到的行数。
const CREDENTIAL_LINE = /^([^\s:|]+@[^\s:|]+?)(?:----|[\t|:]|\s+)(.+)$/;
const EMAIL_ONLY_LINE = /^([^\s:|@]+@[^\s:|@]+\.[^\s:|@-]+)$/;
const POLL_INTERVAL_MS = 2000;

function importErrorMessage(error?: string | null, detail?: string) {
  if (detail?.includes("invalid_state") || error === "invalid_state") {
    return "上游登录会话已失效，请重试或使用浏览器 OAuth 授权导入。";
  }
  if (error === "otp_not_received") {
    return "等待验证码超时：程序未能在设定时间内读到本次登录的有效验证码。邮件可能延迟到达，请核对收件时间和收件路由后重试，或使用浏览器 OAuth 授权导入。";
  }
  if (error?.startsWith("profile_completion_failed") || error === "profile_no_continue_url") {
    return "注册资料未能提交成功，请使用浏览器 OAuth 授权完成注册后导入。";
  }
  if (error?.startsWith("no_mailbox_route")) {
    return "未配置这个邮箱的收件路由，请先到「巡检 → 邮箱与路由」配置。";
  }
  if (error === "rate_limit_exceeded") {
    return "触发 OpenAI 频率限制，请稍后或调大间隔再试。";
  }
  if (error === "signup_csrf_failed_403") {
    return "chatgpt.com 注册入口被 Cloudflare 拦截（403），需开启 FlareSolverr 过盾。";
  }
  return error || detail;
}

function randomAliasLocal(prefix: string) {
  const letters = "abcdefghijklmnopqrstuvwxyz";
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  const rand = (len: number) =>
    Array.from({ length: len }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
  const cleanPrefix = prefix
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]/g, "")
    .replace(/^[._-]+|[._-]+$/g, "");
  if (cleanPrefix) {
    return `${cleanPrefix}_${rand(6)}`;
  }
  return `${letters[Math.floor(Math.random() * letters.length)]}${rand(7)}`;
}

function countCredentialLines(text: string) {
  const seen = new Set<string>();
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }
    const match = CREDENTIAL_LINE.exec(line);
    const bare = match ? null : EMAIL_ONLY_LINE.exec(line.replace(/[-:|\t ]+$/, ""));
    const email = match && match[2].trim() ? match[1] : bare?.[1];
    if (email) {
      seen.add(email.trim().toLowerCase());
    }
  }
  return seen.size;
}

type PasswordImportDialogProps = {
  disabled?: boolean;
  onFinished: () => void;
  /** Pass to control the dialog from outside; the built-in trigger button is then not rendered. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Reports import progress so a page can show it while the dialog is closed. */
  onProgress?: (progress: { done: number; total: number } | null) => void;
};

export function PasswordImportDialog({ disabled, onFinished, open: openProp, onOpenChange, onProgress }: PasswordImportDialogProps) {
  const [innerOpen, setInnerOpen] = useState(false);
  const controlled = openProp !== undefined;
  const open = controlled ? openProp : innerOpen;
  const [text, setText] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [job, setJob] = useState<PasswordImportJob | null>(null);
  const [domains, setDomains] = useState<string[]>([]);
  const [selectedDomain, setSelectedDomain] = useState("");
  const [aliasPrefix, setAliasPrefix] = useState("");
  const [aliasCount, setAliasCount] = useState("5");
  const timerRef = useRef<number | null>(null);
  const onFinishedRef = useRef(onFinished);

  // When controlled, a finished job stays visible on reopen; its "继续导入" button starts a new one.
  const setOpen = (next: boolean) => (controlled ? onOpenChange?.(next) : setInnerOpen(next));

  useEffect(() => {
    onFinishedRef.current = onFinished;
  }, [onFinished]);

  useEffect(() => {
    let active = true;
    void fetchLatestKeeperJob()
      .then((res) => {
        if (active && res.job && res.job.state === "running") {
          setJob((prev) => prev ?? res.job);
        }
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    let active = true;
    void fetchMailboxSettings()
      .then((settings) => {
        if (!active) {
          return;
        }
        const cfProviders = new Set(
          Object.entries(settings.providers || {})
            .filter(([, p]) => p.kind === "cloudflare_temp_email")
            .map(([name]) => name),
        );
        const list: string[] = [];
        const cfList: string[] = [];
        for (const route of settings.routes || []) {
          if (route.allow_register === false) {
            continue;
          }
          const match = String(route.match || "").trim().replace(/^@+/, "").toLowerCase();
          if (!match || match === "*" || match.includes("@") || !match.includes(".")) {
            continue;
          }
          if (!list.includes(match)) {
            list.push(match);
          }
          if (cfProviders.has(route.provider) && !cfList.includes(match)) {
            cfList.push(match);
          }
        }
        const ordered = [...cfList, ...list.filter((d) => !cfList.includes(d))];
        setDomains(ordered);
        setSelectedDomain((prev) => (prev && ordered.includes(prev) ? prev : ordered[0] || ""));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [open]);

  const handleGenerateAliases = () => {
    const domain = selectedDomain.trim().replace(/^@+/, "");
    if (!domain) {
      toast.error("请先在「巡检 → 邮箱与路由」配置收件域名");
      return;
    }
    const count = Math.min(50, Math.max(1, Math.round(Number(aliasCount) || 1)));
    const existing = new Set(
      text
        .split(/\r?\n/)
        .map((line) => line.trim().toLowerCase())
        .filter(Boolean),
    );
    const generated: string[] = [];
    let guard = 0;
    while (generated.length < count && guard < count * 10) {
      guard += 1;
      const email = `${randomAliasLocal(aliasPrefix)}@${domain}`;
      if (!existing.has(email.toLowerCase())) {
        existing.add(email.toLowerCase());
        generated.push(email);
      }
    }
    const block = generated.join("\n");
    setText((prev) => (prev.trim() ? `${prev.trimEnd()}\n${block}` : block));
  };

  const isRunning = job?.state === "running";
  const lineCount = countCredentialLines(text);
  const progress = job && job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
  const okCount = job?.items.filter((item) => item.ok).length ?? 0;
  const failCount = (job?.items.length ?? 0) - okCount;
  const onProgressRef = useRef(onProgress);

  useEffect(() => {
    onProgressRef.current = onProgress;
  }, [onProgress]);

  useEffect(() => {
    onProgressRef.current?.(isRunning && job ? { done: job.done, total: job.total } : null);
  }, [isRunning, job]);

  useEffect(() => {
    if (!job || job.state !== "running") {
      return;
    }
    const jobId = job.id;
    timerRef.current = window.setTimeout(() => {
      void fetchKeeperJob(jobId)
        .then((next) => {
          setJob(next);
          if (next.state === "finished") {
            const ok = next.items.filter((item) => item.ok).length;
            const failed = next.items.length - ok;
            if (failed > 0) {
              toast.warning(`邮箱导入完成：成功 ${ok} 个，失败 ${failed} 个`);
            } else {
              toast.success(`邮箱导入完成：成功 ${ok} 个`);
            }
            onFinishedRef.current();
          }
        })
        .catch((error) => {
          toast.error(error instanceof Error ? error.message : "查询导入进度失败");
          setJob((prev) => (prev && prev.id === jobId ? { ...prev, state: "finished" } : prev));
        });
    }, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [job]);

  const handleSubmit = async () => {
    if (lineCount === 0) {
      toast.error("没有识别到邮箱");
      return;
    }
    setIsSubmitting(true);
    try {
      const started = await startPasswordImport(text);
      setJob(started);
      setText("");
      toast.success(`已开始导入 ${started.total} 个账号`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动导入失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  const openDialog = () => {
    if (job && !isRunning) {
      setJob(null);
    }
    setOpen(true);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {controlled ? null : (
        <Button
          variant="outline"
          className="h-10 rounded-xl border-stone-200 bg-white/80 px-4 text-stone-700 hover:bg-white"
          onClick={openDialog}
          disabled={disabled}
        >
          {isRunning ? <LoaderCircle className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
          {isRunning && job ? `邮箱导入 ${job.done}/${job.total}` : "邮箱登录 / 注册导入"}
        </Button>
      )}
      <DialogContent showCloseButton={false} className="max-h-[90vh] overflow-y-auto rounded-2xl p-6">
        <DialogHeader className="gap-2">
          <DialogTitle>邮箱登录 / 注册导入</DialogTitle>
          <DialogDescription className="text-sm leading-6">
            一行一个邮箱。系统根据 OpenAI 返回的流程登录已有账号，或继续新邮箱的注册，成功后保存自动续期凭据并加入号池。
          </DialogDescription>
        </DialogHeader>

        {!job ? (
          <div className="space-y-2 rounded-xl border border-stone-200 bg-stone-50 p-4 text-sm leading-6 text-stone-600">
            <p><span className="font-medium text-stone-800">已有账号：</span>使用邮箱验证码登录；有密码也可填写「邮箱----密码」。</p>
            <p><span className="font-medium text-stone-800">全新邮箱：</span>上游进入注册流程并通过验证码后，自动填写 8 位随机英文字母姓名和 20–60 岁随机年龄，再完成注册和导入。</p>
            <p>只填写邮箱时，需先在「巡检 → 邮箱与路由」配置收件。未收到验证码或注册失败时，可从「添加账号 → 导入 Token / Session 等 → OAuth 登录」使用浏览器完成授权。</p>
          </div>
        ) : null}

        {job ? (
          <div className="space-y-3">
            <div className="rounded-lg border border-stone-200 bg-white px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-medium text-stone-700">
                  {isRunning ? "正在登录 / 注册并导入" : "已完成"}，已处理 {job.done}/{job.total}
                </div>
                <Badge variant={isRunning ? "info" : failCount > 0 ? "warning" : "success"} className="rounded-md">
                  {progress}%
                </Badge>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-stone-200">
                <div className="h-full rounded-full bg-stone-900 transition-all" style={{ width: `${progress}%` }} />
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-xs text-stone-500">
                <span>成功 {okCount}</span>
                <span>失败 {failCount}</span>
                {isRunning ? <span>账号之间会随机间隔几十秒，避免触发风控；可以关闭窗口，后台继续导入。</span> : null}
              </div>
            </div>
            {job.items.length > 0 ? (
              <div className="max-h-[320px] divide-y divide-stone-100 overflow-auto rounded-xl border border-stone-200">
                {job.items.map((item, index) => (
                  <div key={`${item.email}-${index}`} className="flex items-start gap-3 px-4 py-2.5">
                    {item.ok ? (
                      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                    ) : (
                      <CircleAlert className="mt-0.5 size-4 shrink-0 text-rose-500" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-stone-700">{item.email}</span>
                        {item.ok ? (
                          <Badge variant={item.skipped ? "secondary" : "success"} className="rounded-md">
                            {item.skipped ? "已在号池，跳过" : item.updated ? "已更新" : "已新增"}
                          </Badge>
                        ) : (
                          <Badge variant="danger" className="rounded-md">
                            失败
                          </Badge>
                        )}
                      </div>
                      {!item.ok && (item.error || item.detail) ? (
                        <div className="text-xs break-all text-rose-600" title={item.detail || item.error || undefined}>
                          {importErrorMessage(item.error, item.detail)}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="space-y-3">
            {domains.length > 0 ? (
              <div className="flex flex-wrap items-center gap-2 rounded-xl border border-stone-200 bg-stone-50/70 p-2.5">
                <Select value={selectedDomain} onValueChange={setSelectedDomain}>
                  <SelectTrigger className="h-8 w-36 rounded-lg border-stone-200 bg-white text-xs">
                    <SelectValue placeholder="选择域名" />
                  </SelectTrigger>
                  <SelectContent>
                    {domains.map((domain) => (
                      <SelectItem key={domain} value={domain} className="text-xs">
                        @{domain}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input
                  value={aliasPrefix}
                  onChange={(event) => setAliasPrefix(event.target.value)}
                  placeholder="可选前缀"
                  className="h-8 w-24 rounded-lg border-stone-200 bg-white text-xs"
                />
                <div className="flex items-center gap-1">
                  <Input
                    type="number"
                    min={1}
                    max={50}
                    value={aliasCount}
                    onChange={(event) => setAliasCount(event.target.value)}
                    className="h-8 w-16 rounded-lg border-stone-200 bg-white text-center text-xs"
                  />
                  <span className="text-xs text-stone-500">个</span>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleGenerateAliases}
                  className="ml-auto h-8 rounded-lg border-stone-200 bg-white px-2.5 text-xs text-stone-700 hover:bg-stone-100"
                >
                  <Wand2 className="size-3.5" />
                  生成随机邮箱
                </Button>
              </div>
            ) : null}
            <Textarea
              placeholder={"abc@040601.xyz\nsomeone@duck.com\nanother@example.com----password"}
              value={text}
              onChange={(event) => setText(event.target.value)}
              className="min-h-48 rounded-xl border-stone-200 font-mono text-xs"
            />
            <div className="text-xs text-stone-500">识别到 {lineCount} 个账号（同一邮箱只取第一行；号池里已有且没坏的账号会跳过，状态为「异常」的会重新登录）。</div>
          </div>
        )}

        <DialogFooter className="pt-2">
          <Button
            variant="secondary"
            className="h-10 rounded-xl bg-stone-100 px-5 text-stone-700 hover:bg-stone-200"
            onClick={() => setOpen(false)}
            disabled={isSubmitting}
          >
            {job ? "关闭" : "取消"}
          </Button>
          {job && !isRunning ? (
            <Button className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800" onClick={() => setJob(null)}>
              继续导入
            </Button>
          ) : null}
          {!job ? (
            <Button
              className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800"
              onClick={() => void handleSubmit()}
              disabled={isSubmitting || lineCount === 0}
            >
              {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
              开始导入
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
