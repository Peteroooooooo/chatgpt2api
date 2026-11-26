"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Play, RefreshCw, Save, Square, Undo2, UserPlus } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchMailboxSettings,
  fetchRegisterRun,
  fetchRegisterRuns,
  runAutoRegisterNow,
  saveKeeperSettings,
  stopAutoRegister,
  type KeeperSettings,
  type KeeperSettingsResponse,
  type ManualRegisterOptions,
  type RegisterOutcome,
  type RegisterRun,
  type RegisterRunDetail,
} from "@/lib/api";

type BadgeVariant = "success" | "info" | "danger" | "warning" | "secondary";

const OUTCOME_META: Record<RegisterOutcome, { label: string; variant: BadgeVariant }> = {
  registered: { label: "新注册", variant: "success" },
  existing_added: { label: "已存在入池", variant: "info" },
  skipped: { label: "跳过", variant: "secondary" },
  failed: { label: "失败", variant: "danger" },
};

const OUTCOME_ORDER: RegisterOutcome[] = ["registered", "existing_added", "skipped", "failed"];

const STATE_META: Record<string, { label: string; variant: BadgeVariant }> = {
  running: { label: "进行中", variant: "info" },
  finished: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "danger" },
};

const TRIGGER_LABELS: Record<string, string> = {
  manual: "手动",
  schedule: "定时",
};

function Toggle({ checked, onChange }: { checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${checked ? "bg-stone-900" : "bg-stone-300"}`}
    >
      <span
        className={`absolute top-0.5 left-0.5 size-4 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-4" : ""}`}
      />
    </button>
  );
}

function formatTime(value?: string | null) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatCountdown(targetIso?: string | null, nowMs = Date.now()) {
  if (!targetIso) {
    return "";
  }
  const diff = Math.max(0, Math.round((new Date(targetIso).getTime() - nowMs) / 1000));
  if (!Number.isFinite(diff) || diff <= 0) {
    return "即将执行";
  }
  const mins = Math.floor(diff / 60);
  const secs = diff % 60;
  return mins > 0 ? `${mins} 分 ${secs} 秒` : `${secs} 秒`;
}

function RegisterCounts({ run }: { run: RegisterRun }) {
  const counts = run.counts || {};
  const items = OUTCOME_ORDER.filter((key) => (counts[key] ?? 0) > 0);
  if (items.length === 0) {
    return <span className="text-xs text-stone-400">—</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((key) => (
        <Badge key={key} variant={OUTCOME_META[key].variant} className="rounded-md whitespace-nowrap">
          {OUTCOME_META[key].label} {counts[key]}
        </Badge>
      ))}
    </div>
  );
}

type RegisterDraft = {
  enabled: boolean;
  time: string;
  domain: string;
  prefix: string;
  dailyCount: string;
  intervalMinutes: string;
  maxNormal: string;
};

function draftFromSaved(saved: KeeperSettings): RegisterDraft {
  return {
    enabled: Boolean(saved.auto_register_enabled),
    time: saved.auto_register_time || "03:00",
    domain: saved.auto_register_domain || "",
    prefix: saved.auto_register_prefix || "",
    dailyCount: String(saved.auto_register_daily_count ?? 2),
    intervalMinutes: String(saved.auto_register_interval_minutes ?? 60),
    maxNormal: String(saved.auto_register_max_normal_accounts ?? 50),
  };
}

function RegisterSettingsCard({
  saved,
  routeDomains,
  onSaved,
}: {
  saved: KeeperSettingsResponse;
  routeDomains: string[];
  onSaved: (data: KeeperSettingsResponse) => void;
}) {
  const [draft, setDraft] = useState<RegisterDraft>(() => draftFromSaved(saved));
  const [isSaving, setIsSaving] = useState(false);

  const dirty = useMemo(() => {
    const base = draftFromSaved(saved);
    return (
      draft.enabled !== base.enabled ||
      draft.time !== base.time ||
      draft.domain.trim() !== base.domain.trim() ||
      draft.prefix.trim() !== base.prefix.trim() ||
      draft.dailyCount.trim() !== base.dailyCount ||
      draft.intervalMinutes.trim() !== base.intervalMinutes ||
      draft.maxNormal.trim() !== base.maxNormal
    );
  }, [draft, saved]);

  const reset = () => setDraft(draftFromSaved(saved));

  const toggleDomainChip = (domain: string) => {
    const parts = draft.domain
      .split(/[\s,;]+/)
      .map((item) => item.trim().replace(/^@+/, "").toLowerCase())
      .filter(Boolean);
    const next = parts.includes(domain) ? parts.filter((item) => item !== domain) : [...parts, domain];
    setDraft((prev) => ({ ...prev, domain: next.join(", ") }));
  };

  const handleSave = async () => {
    if (!/^\d{1,2}:\d{2}$/.test(draft.time || "")) {
      toast.error("请填写定时注册时间，格式 HH:MM");
      return;
    }
    const dailyCount = Number(draft.dailyCount);
    const intervalMinutes = Number(draft.intervalMinutes);
    const maxNormal = Number(draft.maxNormal);
    if (draft.dailyCount.trim() === "" || !Number.isFinite(dailyCount) || dailyCount < 0) {
      toast.error("「每日注册数量」需要填一个不小于 0 的数字");
      return;
    }
    if (draft.intervalMinutes.trim() === "" || !Number.isFinite(intervalMinutes) || intervalMinutes < 0) {
      toast.error("「注册间隔」需要填一个不小于 0 的数字");
      return;
    }
    if (draft.maxNormal.trim() === "" || !Number.isFinite(maxNormal) || maxNormal < 0) {
      toast.error("「正常账号上限」需要填一个不小于 0 的数字");
      return;
    }
    if (draft.enabled && !draft.domain.trim()) {
      toast.error("启用定时注册前，请先填写目标邮箱域名");
      return;
    }

    setIsSaving(true);
    try {
      const updated = await saveKeeperSettings({
        auto_register_enabled: draft.enabled,
        auto_register_time: draft.time,
        auto_register_domain: draft.domain.trim(),
        auto_register_prefix: draft.prefix.trim(),
        auto_register_daily_count: Math.round(dailyCount),
        auto_register_interval_minutes: Math.round(intervalMinutes),
        auto_register_max_normal_accounts: Math.round(maxNormal),
      });
      onSaved(updated);
      toast.success("定时注册设置已保存，立即生效");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存定时注册设置失败");
    } finally {
      setIsSaving(false);
    }
  };

  const selectedDomains = useMemo(
    () =>
      new Set(
        draft.domain
          .split(/[\s,;]+/)
          .map((item) => item.trim().replace(/^@+/, "").toLowerCase())
          .filter(Boolean),
      ),
    [draft.domain],
  );

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="divide-y divide-stone-100 p-0">
        <div className="grid gap-4 px-6 py-5 md:grid-cols-[220px_1fr] md:gap-8">
          <div>
            <h3 className="text-sm font-semibold text-stone-800">每日定时注册</h3>
            <p className="mt-1 text-xs leading-5 text-stone-500">
              每天按指定时间自动生成随机别名注册并入池，遇到 429 限流或连续失败 2 次自动中止当天任务。
            </p>
          </div>
          <div className="max-w-3xl space-y-4">
            <label className="flex cursor-pointer items-start justify-between gap-6">
              <div>
                <div className="text-sm font-medium text-stone-700">启用每日定时注册</div>
                <div className="text-xs leading-5 text-stone-500">
                  {saved.auto_register_enabled && saved.next_register_at
                    ? `下次预计执行：${formatTime(saved.next_register_at)}`
                    : "关闭定时后，仍可通过下方「手动执行」临时注册。"}
                </div>
              </div>
              <Toggle checked={draft.enabled} onChange={(value) => setDraft((prev) => ({ ...prev, enabled: value }))} />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <label className="text-sm font-medium text-stone-700">执行时间</label>
                <Input
                  type="time"
                  value={draft.time}
                  onChange={(event) => setDraft((prev) => ({ ...prev, time: event.target.value }))}
                  className="h-10 rounded-xl border-stone-200 bg-white"
                />
                <div className="text-xs text-stone-400">北京时间，建议与 04:30 巡检错开。</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-sm font-medium text-stone-700">账号间隔（默认 60 分钟）</label>
                <div className="relative">
                  <Input
                    type="number"
                    min={0}
                    value={draft.intervalMinutes}
                    onChange={(event) => setDraft((prev) => ({ ...prev, intervalMinutes: event.target.value }))}
                    className="h-10 rounded-xl border-stone-200 bg-white pr-14"
                  />
                  <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs text-stone-400">
                    分钟
                  </span>
                </div>
                <div className="text-xs leading-5 text-stone-400">默认 60 分钟（1 小时）一个；填 0 则用 20–60 秒短间隔。</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-sm font-medium text-stone-700">每日注册数量</label>
                <div className="relative">
                  <Input
                    type="number"
                    min={0}
                    value={draft.dailyCount}
                    onChange={(event) => setDraft((prev) => ({ ...prev, dailyCount: event.target.value }))}
                    className="h-10 rounded-xl border-stone-200 bg-white pr-12"
                  />
                  <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs text-stone-400">
                    个
                  </span>
                </div>
                <div className="text-xs leading-5 text-stone-400">每天最多尝试注册这么多新号。</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-sm font-medium text-stone-700">号池正常账号上限</label>
                <div className="relative">
                  <Input
                    type="number"
                    min={0}
                    value={draft.maxNormal}
                    onChange={(event) => setDraft((prev) => ({ ...prev, maxNormal: event.target.value }))}
                    className="h-10 rounded-xl border-stone-200 bg-white pr-12"
                  />
                  <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs text-stone-400">
                    个
                  </span>
                </div>
                <div className="text-xs leading-5 text-stone-400">号池正常号达到上限即跳过；填 0 不限制。</div>
              </div>

              <div className="space-y-1.5 sm:col-span-2">
                <label className="text-sm font-medium text-stone-700">目标邮箱域名（后缀）</label>
                <Input
                  value={draft.domain}
                  onChange={(event) => setDraft((prev) => ({ ...prev, domain: event.target.value }))}
                  placeholder="填域名后缀，如 040601.xyz（多个用逗号分隔，会轮流使用）"
                  className="h-10 rounded-xl border-stone-200 bg-white"
                />
                {routeDomains.length > 0 ? (
                  <div className="flex flex-wrap items-center gap-1.5 pt-1">
                    <span className="text-xs text-stone-400">可随机注册域名：</span>
                    {routeDomains.map((domain) => {
                      const active = selectedDomains.has(domain);
                      return (
                        <button
                          key={domain}
                          type="button"
                          onClick={() => toggleDomainChip(domain)}
                          className={`rounded-md border px-2 py-0.5 text-xs transition-colors ${
                            active
                              ? "border-stone-900 bg-stone-900 text-white"
                              : "border-stone-200 bg-stone-50 text-stone-600 hover:bg-stone-100"
                          }`}
                        >
                          {domain}
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-xs text-stone-400">需先在「邮箱与路由」配置域名并勾选「随机注册」。</div>
                )}
              </div>

              <div className="space-y-1.5 sm:col-span-2">
                <label className="text-sm font-medium text-stone-700">邮箱前缀（可选）</label>
                <Input
                  value={draft.prefix}
                  onChange={(event) => setDraft((prev) => ({ ...prev, prefix: event.target.value }))}
                  placeholder="留空则纯随机 8 位字母数字；填写如 auto 则生成 auto_xxxxxx@域名"
                  className="h-10 rounded-xl border-stone-200 bg-white"
                />
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 px-6 py-4">
          <span className={`mr-auto text-xs ${dirty ? "text-amber-600" : "text-stone-400"}`}>
            {dirty ? "有未保存的修改" : "保存后立即生效，不用重启"}
          </span>
          {dirty ? (
            <Button variant="ghost" className="h-9 rounded-xl px-3 text-stone-600" onClick={reset} disabled={isSaving}>
              <Undo2 className="size-4" />
              撤销
            </Button>
          ) : null}
          <Button
            className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
            onClick={() => void handleSave()}
            disabled={!dirty || isSaving}
          >
            {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
            保存
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

const EMAIL_ADDRESS_LINE = /^([^\s:|@]+@[^\s:|@]+\.[^\s:|@-]+)$/;

function countValidEmails(raw: string) {
  const seen = new Set<string>();
  for (const part of raw.split(/[\r\n,;]+/)) {
    const clean = part.trim();
    if (!clean || clean.startsWith("#")) {
      continue;
    }
    const match = EMAIL_ADDRESS_LINE.exec(clean);
    if (match) {
      seen.add(match[1].toLowerCase());
    }
  }
  return seen.size;
}

function ManualRegisterCard({
  routeDomains,
  defaultDomain,
  defaultPrefix,
  hasRunning,
  isStarting,
  onRun,
}: {
  routeDomains: string[];
  defaultDomain: string;
  defaultPrefix: string;
  hasRunning: boolean;
  isStarting: boolean;
  onRun: (options: ManualRegisterOptions) => void;
}) {
  const [mode, setMode] = useState<"domain" | "emails">("domain");
  const [domain, setDomain] = useState(() => defaultDomain || routeDomains[0] || "");
  const [prefix, setPrefix] = useState(() => defaultPrefix || "");
  const [count, setCount] = useState("1");
  const [intervalMinutes, setIntervalMinutes] = useState("0");
  const [emailsText, setEmailsText] = useState("");
  const domainInitializedRef = useRef(Boolean(defaultDomain));

  useEffect(() => {
    if (!domainInitializedRef.current && !domain && (defaultDomain || routeDomains[0])) {
      domainInitializedRef.current = true;
      setDomain(defaultDomain || routeDomains[0] || "");
    }
  }, [defaultDomain, routeDomains, domain]);

  const selectedDomains = useMemo(
    () =>
      new Set(
        domain
          .split(/[\s,;]+/)
          .map((item) => item.trim().replace(/^@+/, "").toLowerCase())
          .filter(Boolean),
      ),
    [domain],
  );

  const toggleDomainChip = (target: string) => {
    const parts = domain
      .split(/[\s,;]+/)
      .map((item) => item.trim().replace(/^@+/, "").toLowerCase())
      .filter(Boolean);
    const next = parts.includes(target) ? parts.filter((item) => item !== target) : [...parts, target];
    setDomain(next.join(", "));
  };

  const emailCount = useMemo(() => countValidEmails(emailsText), [emailsText]);

  const handleSubmit = () => {
    const interval = Number(intervalMinutes);
    if (intervalMinutes.trim() === "" || !Number.isFinite(interval) || interval < 0) {
      toast.error("「账号间隔」需要填一个不小于 0 的数字");
      return;
    }
    if (mode === "emails") {
      if (emailCount === 0) {
        toast.error("请至少填写一个有效邮箱地址");
        return;
      }
      onRun({
        emails: emailsText,
        interval_minutes: Math.round(interval),
      });
      return;
    }

    if (!domain.trim()) {
      toast.error("请先填写或选择一个目标邮箱域名");
      return;
    }
    const parsedCount = Number(count);
    if (count.trim() === "" || !Number.isFinite(parsedCount) || parsedCount < 1) {
      toast.error("「本次注册数量」至少为 1");
      return;
    }
    onRun({
      domain: domain.trim(),
      prefix: prefix.trim(),
      count: Math.round(parsedCount),
      interval_minutes: Math.round(interval),
    });
  };

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="divide-y divide-stone-100 p-0">
        <div className="grid gap-4 px-6 py-5 md:grid-cols-[220px_1fr] md:gap-8">
          <div>
            <h3 className="text-sm font-semibold text-stone-800">手动执行（独立临时参数）</h3>
            <p className="mt-1 text-xs leading-5 text-stone-500">
              临时指定域名或完整邮箱立即注册，不会覆盖或清空上方的每日定时设置。
            </p>
          </div>
          <div className="max-w-3xl space-y-4">
            <div className="inline-flex rounded-xl bg-stone-100 p-1">
              <button
                type="button"
                onClick={() => setMode("domain")}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  mode === "domain" ? "bg-white text-stone-900 shadow-xs" : "text-stone-600 hover:text-stone-900"
                }`}
              >
                按域名随机生成
              </button>
              <button
                type="button"
                onClick={() => setMode("emails")}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  mode === "emails" ? "bg-white text-stone-900 shadow-xs" : "text-stone-600 hover:text-stone-900"
                }`}
              >
                指定完整邮箱
              </button>
            </div>

            {mode === "domain" ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5 sm:col-span-2">
                  <label className="text-sm font-medium text-stone-700">临时目标域名（后缀）</label>
                  <Input
                    value={domain}
                    onChange={(event) => setDomain(event.target.value)}
                    placeholder="例如：peteroooooooo.com（只影响本次手动执行）"
                    className="h-10 rounded-xl border-stone-200 bg-white"
                  />
                  {routeDomains.length > 0 ? (
                    <div className="flex flex-wrap items-center gap-1.5 pt-1">
                      <span className="text-xs text-stone-400">可随机注册域名：</span>
                      {routeDomains.map((item) => {
                        const active = selectedDomains.has(item);
                        return (
                          <button
                            key={item}
                            type="button"
                            onClick={() => toggleDomainChip(item)}
                            className={`rounded-md border px-2 py-0.5 text-xs transition-colors ${
                              active
                                ? "border-stone-900 bg-stone-900 text-white"
                                : "border-stone-200 bg-stone-50 text-stone-600 hover:bg-stone-100"
                            }`}
                          >
                            {item}
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>

                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-stone-700">邮箱前缀（可选）</label>
                  <Input
                    value={prefix}
                    onChange={(event) => setPrefix(event.target.value)}
                    placeholder="留空纯随机；如 auto 则生成 auto_xxxxxx"
                    className="h-10 rounded-xl border-stone-200 bg-white"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <label className="text-sm font-medium text-stone-700">本次数量</label>
                    <Input
                      type="number"
                      min={1}
                      value={count}
                      onChange={(event) => setCount(event.target.value)}
                      className="h-10 rounded-xl border-stone-200 bg-white"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-sm font-medium text-stone-700">间隔（分钟）</label>
                    <Input
                      type="number"
                      min={0}
                      value={intervalMinutes}
                      onChange={(event) => setIntervalMinutes(event.target.value)}
                      className="h-10 rounded-xl border-stone-200 bg-white"
                    />
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-stone-700">完整邮箱地址</label>
                    <span className="text-xs text-stone-400">
                      {emailCount > 0 ? `已识别 ${emailCount} 个邮箱` : "每行一个完整邮箱"}
                    </span>
                  </div>
                  <Textarea
                    value={emailsText}
                    onChange={(event) => setEmailsText(event.target.value)}
                    placeholder={"例如：\nmy_alias123@duck.com\ncustom_name@040601.xyz"}
                    rows={4}
                    className="rounded-xl border-stone-200 bg-white font-mono text-xs"
                  />
                  <div className="text-xs leading-5 text-stone-400">
                    只要邮箱能在「邮箱与路由」匹配到收件箱，即使该域名关闭了「随机注册」（如 duck.com），这里也能直接注册并入池。
                  </div>
                </div>
                <div className="max-w-44 space-y-1.5">
                  <label className="text-sm font-medium text-stone-700">多个时账号间隔（分钟）</label>
                  <Input
                    type="number"
                    min={0}
                    value={intervalMinutes}
                    onChange={(event) => setIntervalMinutes(event.target.value)}
                    className="h-10 rounded-xl border-stone-200 bg-white"
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 px-6 py-4">
          <span className="text-xs text-stone-400">
            间隔填 0 则使用 20–60 秒短延迟；走真浏览器注册并直接入池。
          </span>
          <Button
            onClick={handleSubmit}
            disabled={isStarting || hasRunning}
            className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
          >
            {isStarting || hasRunning ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}
            {hasRunning ? "注册进行中" : "立即执行"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function RegisterDetailDialog({
  runId,
  open,
  onOpenChange,
  refreshKey,
}: {
  runId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  refreshKey: number;
}) {
  const [detail, setDetail] = useState<RegisterRunDetail | null>(null);

  useEffect(() => {
    if (!open || !runId) {
      return;
    }
    let active = true;
    void fetchRegisterRun(runId)
      .then((data) => {
        if (active) {
          setDetail(data);
        }
      })
      .catch((error) => toast.error(error instanceof Error ? error.message : "加载注册详情失败"));
    return () => {
      active = false;
    };
  }, [open, runId, refreshKey]);

  const current = detail && detail.id === runId ? detail : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(82vh,760px)] w-[min(92vw,960px)] max-w-none flex-col overflow-hidden rounded-2xl p-0 sm:max-w-none">
        <DialogHeader className="shrink-0 border-b border-stone-100 px-6 py-5">
          <DialogTitle>定时注册详情</DialogTitle>
          <DialogDescription className="text-sm leading-6">
            {current
              ? `${formatTime(current.started_at)} · ${TRIGGER_LABELS[current.trigger] || current.trigger} · 已处理 ${current.done}/${current.total}`
              : runId}
          </DialogDescription>
        </DialogHeader>
        {current && (current.skip_reason || current.stopped_reason || current.error) ? (
          <div className="border-b border-stone-100 bg-stone-50 px-6 py-3 text-xs text-stone-600">
            {current.skip_reason || current.stopped_reason || current.error}
          </div>
        ) : null}
        <div className="flex-1 overflow-auto">
          {current && current.entries.length > 0 ? (
            <Table className="min-w-[720px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-44">时间</TableHead>
                  <TableHead>邮箱</TableHead>
                  <TableHead className="w-28">结果</TableHead>
                  <TableHead>错误 / 说明</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {current.entries.map((entry, index) => {
                  const meta = OUTCOME_META[entry.outcome] ?? { label: entry.outcome, variant: "secondary" as const };
                  return (
                    <TableRow key={`${entry.email}-${index}`} className="text-stone-600">
                      <TableCell className="text-xs whitespace-nowrap">{formatTime(entry.at)}</TableCell>
                      <TableCell className="font-mono text-xs text-stone-800">{entry.email}</TableCell>
                      <TableCell>
                        <Badge variant={meta.variant} className="rounded-md whitespace-nowrap">
                          {meta.label}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-[360px] text-xs break-all text-rose-600">
                        {entry.error ? `${entry.error}${entry.detail ? ` (${entry.detail})` : ""}` : ""}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <div className="px-6 py-14 text-center text-sm text-stone-500">
              {current ? current.skip_reason || "本次没有注册记录" : "正在加载…"}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function AutoRegisterTab({
  saved,
  onSaved,
}: {
  saved: KeeperSettingsResponse | null;
  onSaved: (data: KeeperSettingsResponse) => void;
}) {
  const [runs, setRuns] = useState<RegisterRun[]>([]);
  const [routeDomains, setRouteDomains] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isStarting, setIsStarting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [detailRunId, setDetailRunId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const timerRef = useRef<number | null>(null);

  const loadRuns = useCallback(async (silent = false) => {
    if (!silent) {
      setIsLoading(true);
    }
    try {
      const data = await fetchRegisterRuns();
      setRuns(data.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载定时注册记录失败");
    } finally {
      if (!silent) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetchRegisterRuns()
      .then((data) => {
        if (active) {
          setRuns(data.items);
        }
      })
      .catch((error) => toast.error(error instanceof Error ? error.message : "加载定时注册记录失败"))
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });
    void fetchMailboxSettings()
      .then((mb) => {
        if (!active) {
          return;
        }
        const list: string[] = [];
        for (const route of mb.routes || []) {
          if (route.allow_register === false) {
            continue;
          }
          const match = String(route.match || "").trim().replace(/^@+/, "").toLowerCase();
          if (match && match !== "*" && !match.includes("@") && match.includes(".") && !list.includes(match)) {
            list.push(match);
          }
        }
        setRouteDomains(list);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const runningRun = runs.find((run) => run.state === "running") || null;
  const hasRunning = Boolean(runningRun);

  useEffect(() => {
    if (!hasRunning) {
      return;
    }
    timerRef.current = window.setInterval(() => {
      setNowMs(Date.now());
      void loadRuns(true);
      setRefreshKey((value) => value + 1);
    }, 3000);
    return () => {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
      setRefreshKey((value) => value + 1);
    };
  }, [hasRunning, loadRuns]);

  const handleRunNow = async (options?: ManualRegisterOptions) => {
    setIsStarting(true);
    try {
      await runAutoRegisterNow(options);
      toast.success("注册任务已启动");
      await loadRuns(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动注册失败");
    } finally {
      setIsStarting(false);
    }
  };

  const handleStop = async () => {
    setIsStopping(true);
    try {
      await stopAutoRegister();
      toast.success("已发送停止信号");
      await loadRuns(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "停止任务失败");
    } finally {
      setIsStopping(false);
    }
  };

  return (
    <div className="space-y-4">
      {runningRun ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-sky-200 bg-sky-50/70 px-5 py-3.5 text-sm">
          <div className="flex items-center gap-2.5 text-sky-900">
            <LoaderCircle className="size-4 shrink-0 animate-spin text-sky-600" />
            <div>
              <span className="font-medium">
                正在注册（{runningRun.done}/{runningRun.total || "—"}）
              </span>
              {runningRun.current_email ? (
                <span className="ml-2 font-mono text-xs text-sky-700">{runningRun.current_email}</span>
              ) : runningRun.next_attempt_at ? (
                <span className="ml-2 text-xs text-sky-700">
                  等待下一轮间隔，还剩 {formatCountdown(runningRun.next_attempt_at, nowMs)}
                </span>
              ) : null}
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleStop()}
            disabled={isStopping}
            className="h-8 rounded-lg border-rose-200 bg-white text-rose-600 hover:bg-rose-50"
          >
            {isStopping ? <LoaderCircle className="size-3.5 animate-spin" /> : <Square className="size-3.5" />}
            停止任务
          </Button>
        </div>
      ) : null}

      {saved ? (
        <>
          <RegisterSettingsCard
            key={`${saved.auto_register_enabled}-${saved.auto_register_time}-${saved.auto_register_domain}-${saved.auto_register_prefix}-${saved.auto_register_daily_count}-${saved.auto_register_interval_minutes}-${saved.auto_register_max_normal_accounts}`}
            saved={saved}
            routeDomains={routeDomains}
            onSaved={onSaved}
          />
          <ManualRegisterCard
            routeDomains={routeDomains}
            defaultDomain={saved.auto_register_domain || ""}
            defaultPrefix={saved.auto_register_prefix || ""}
            hasRunning={hasRunning}
            isStarting={isStarting}
            onRun={(options) => void handleRunNow(options)}
          />
        </>
      ) : (
        <div className="flex items-center justify-center py-14">
          <LoaderCircle className="size-5 animate-spin text-stone-400" />
        </div>
      )}

      <Card className="overflow-hidden rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-100 px-5 py-4">
            <div className="flex items-center gap-2 text-sm text-stone-600">
              <UserPlus className="size-4 text-stone-500" />
              <span>注册记录 {runs.length} 次</span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void loadRuns()}
                disabled={isLoading}
                className="h-8 rounded-lg border-stone-200 bg-white text-xs text-stone-700"
              >
                <RefreshCw className={`size-3.5 ${isLoading ? "animate-spin" : ""}`} />
                刷新
              </Button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <Table className="min-w-[880px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-44">开始时间</TableHead>
                  <TableHead className="w-16">方式</TableHead>
                  <TableHead className="w-36">域名</TableHead>
                  <TableHead className="w-24">状态</TableHead>
                  <TableHead>结果</TableHead>
                  <TableHead>注册账号 / 备注</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((run) => {
                  const state = STATE_META[run.state] ?? { label: run.state, variant: "secondary" as const };
                  const note = run.skip_reason || run.stopped_reason || run.error || "";
                  const emails = (run.emails || []).filter(Boolean);
                  const emailText =
                    emails.length > 2
                      ? `${emails.slice(0, 2).join("、")} 等 ${emails.length} 个`
                      : emails.join("、");
                  const tooltip = [emails.length > 0 ? emails.join("\n") : "", note].filter(Boolean).join("\n");
                  return (
                    <TableRow
                      key={run.id}
                      onClick={() => {
                        setDetailRunId(run.id);
                        setDetailOpen(true);
                      }}
                      className="cursor-pointer text-stone-600 hover:bg-stone-50"
                    >
                      <TableCell className="text-xs whitespace-nowrap">{formatTime(run.started_at)}</TableCell>
                      <TableCell className="text-xs">{TRIGGER_LABELS[run.trigger] || run.trigger}</TableCell>
                      <TableCell className="font-mono text-xs text-stone-700">{run.domain || "—"}</TableCell>
                      <TableCell>
                        <Badge variant={state.variant} className="rounded-md whitespace-nowrap">
                          {state.label}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <RegisterCounts run={run} />
                      </TableCell>
                      <TableCell className="max-w-[360px] text-xs" title={tooltip || undefined}>
                        <div className="space-y-0.5">
                          {emailText ? (
                            <div className="truncate font-mono text-stone-800">{emailText}</div>
                          ) : run.current_email ? (
                            <div className="truncate font-mono text-sky-700">{run.current_email}</div>
                          ) : null}
                          {note ? (
                            <div className="truncate text-stone-500">{note}</div>
                          ) : !emailText && !run.current_email ? (
                            <span className="text-stone-400">—</span>
                          ) : null}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
                {!isLoading && runs.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="py-12 text-center text-sm text-stone-400">
                      还没有注册记录，可在上方「手动执行」随时发起一次测试。
                    </TableCell>
                  </TableRow>
                ) : null}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <RegisterDetailDialog runId={detailRunId} open={detailOpen} onOpenChange={setDetailOpen} refreshKey={refreshKey} />
    </div>
  );
}
