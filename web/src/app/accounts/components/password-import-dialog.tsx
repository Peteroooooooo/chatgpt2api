"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, KeyRound, LoaderCircle } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";
import { fetchKeeperJob, startPasswordImport, type PasswordImportJob } from "@/lib/api";

// 与后端 account_keeper.CREDENTIAL_LINE 一致，只用于提示识别到的行数。
const CREDENTIAL_LINE = /^([^\s:|]+@[^\s:|]+?)(?:----|[\t|:]|\s+)(.+)$/;
const POLL_INTERVAL_MS = 2000;

function countCredentialLines(text: string) {
  const seen = new Set<string>();
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }
    const match = CREDENTIAL_LINE.exec(line);
    if (match && match[2].trim()) {
      seen.add(match[1].trim().toLowerCase());
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
  const timerRef = useRef<number | null>(null);
  const onFinishedRef = useRef(onFinished);

  // When controlled, a finished job stays visible on reopen; its "继续导入" button starts a new one.
  const setOpen = (next: boolean) => (controlled ? onOpenChange?.(next) : setInnerOpen(next));

  useEffect(() => {
    onFinishedRef.current = onFinished;
  }, [onFinished]);

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
              toast.warning(`邮箱密码导入完成：成功 ${ok} 个，失败 ${failed} 个`);
            } else {
              toast.success(`邮箱密码导入完成：成功 ${ok} 个`);
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
      toast.error("没有识别到 邮箱----密码 格式的行");
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
          {isRunning && job ? `邮箱密码导入 ${job.done}/${job.total}` : "邮箱密码导入"}
        </Button>
      )}
      <DialogContent showCloseButton={false} className="max-h-[90vh] overflow-y-auto rounded-2xl p-6">
        <DialogHeader className="gap-2">
          <DialogTitle>邮箱密码导入</DialogTitle>
          <DialogDescription className="text-sm leading-6">
            一行一个账号，格式 邮箱----密码（也支持 : | 制表符 空格 分隔）。登录时如需邮箱验证码会按「邮箱路由」自动读取。
          </DialogDescription>
        </DialogHeader>

        {job ? (
          <div className="space-y-3">
            <div className="rounded-lg border border-stone-200 bg-white px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-medium text-stone-700">
                  {isRunning ? "正在登录" : "已完成"}，已处理 {job.done}/{job.total}
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
                          <Badge variant="success" className="rounded-md">
                            {item.updated ? "已更新" : "已新增"}
                          </Badge>
                        ) : (
                          <Badge variant="danger" className="rounded-md">
                            失败
                          </Badge>
                        )}
                      </div>
                      {!item.ok && (item.error || item.detail) ? (
                        <div className="text-xs break-all text-rose-600" title={item.detail || undefined}>
                          {item.error || item.detail}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="space-y-2">
            <Textarea
              placeholder={"someone@example.com----password\nanother@example.com:password"}
              value={text}
              onChange={(event) => setText(event.target.value)}
              className="min-h-48 rounded-xl border-stone-200 font-mono text-xs"
            />
            <div className="text-xs text-stone-500">识别到 {lineCount} 个账号（同一邮箱只取第一行；已存在的账号会更新密码并重新登录）。</div>
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
