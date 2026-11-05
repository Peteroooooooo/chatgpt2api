"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Play, RefreshCw, Save, ShieldCheck, Undo2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  fetchKeeperSettings,
  fetchPatrolRun,
  fetchPatrolRuns,
  runPatrolNow,
  saveKeeperSettings,
  type KeeperSettings,
  type KeeperSettingsResponse,
  type PatrolEntry,
  type PatrolResult,
  type PatrolRun,
  type PatrolRunDetail,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

import { MailboxRoutingCard } from "./components/mailbox-routing-card";

type BadgeVariant = "success" | "info" | "danger" | "warning" | "secondary";

const RESULT_META: Record<PatrolResult, { label: string; variant: BadgeVariant }> = {
  ok: { label: "正常", variant: "success" },
  relogged_in: { label: "已重登", variant: "info" },
  relogin_failed: { label: "重登失败", variant: "danger" },
  needs_manual: { label: "需人工处理", variant: "warning" },
  skipped: { label: "跳过", variant: "secondary" },
  error: { label: "出错", variant: "danger" },
};

const RESULT_ORDER: PatrolResult[] = ["ok", "relogged_in", "relogin_failed", "needs_manual", "skipped", "error"];
const PROBLEM_RESULTS = new Set<string>(["relogin_failed", "needs_manual", "error"]);

const STATE_META: Record<string, { label: string; variant: BadgeVariant }> = {
  running: { label: "进行中", variant: "info" },
  finished: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "danger" },
};

const TRIGGER_LABELS: Record<string, string> = {
  manual: "手动",
  schedule: "定时",
};

const ACTION_LABELS: Record<string, string> = {
  refresh: "续期",
  verify: "验证",
  relogin: "密码登录",
  relogin_email_code: "验证码登录",
};

type GroupKey = "schedule" | "relogin" | "pace";
type ToggleKey = "patrol_enabled" | "auto_relogin_enabled" | "relogin_without_password";
type NumberKey =
  | "relogin_cooldown_hours"
  | "push_auth_wait_seconds"
  | "relogin_delay_min_seconds"
  | "relogin_delay_max_seconds"
  | "relogin_when_access_token_expires_within_hours"
  | "keep_runs";

const GROUPS: { key: GroupKey; title: string; desc: string }[] = [
  { key: "schedule", title: "定时巡检", desc: "每天把所有账号检查一遍：先续期，续不上就重新登录。" },
  { key: "relogin", title: "自动重新登录", desc: "账号掉线后怎么自动登回来。" },
  { key: "pace", title: "登录节奏", desc: "连续登录多个账号时，每次之间随机等一会儿，降低被风控的概率。" },
];

const TOGGLE_FIELDS: { key: ToggleKey; group: GroupKey; label: string; help: string }[] = [
  { key: "patrol_enabled", group: "schedule", label: "每天定时巡检", help: "关闭后仍可手动「立即巡检」。" },
  {
    key: "auto_relogin_enabled",
    group: "relogin",
    label: "掉线后立即重登",
    help: "平时一发现账号失效就马上重新登录，不用等到巡检时间。",
  },
  {
    key: "relogin_without_password",
    group: "relogin",
    label: "没存密码时用邮箱验证码登录",
    help: "账号的邮箱域名需要在「邮箱与路由」里配好。",
  },
];

const NUMBER_FIELDS: { key: NumberKey; group: GroupKey; label: string; unit: string; help: string }[] = [
  { key: "keep_runs", group: "schedule", label: "保留巡检记录", unit: "次", help: "超出后删除最旧的记录。" },
  {
    key: "relogin_cooldown_hours",
    group: "relogin",
    label: "失败后冷却",
    unit: "小时",
    help: "自动重登失败后隔多久再试。巡检和手动重登不受限制。",
  },
  {
    key: "push_auth_wait_seconds",
    group: "relogin",
    label: "等待手机批准",
    unit: "秒",
    help: "账号开了「在 ChatGPT App 里批准登录」时，先等这么久让你在手机上点批准，没批准就改用邮箱验证码。填 0 直接用验证码，最多 600。",
  },
  {
    key: "relogin_when_access_token_expires_within_hours",
    group: "relogin",
    label: "无续期凭证时提前重登",
    unit: "小时",
    help: "只有 Access Token 的账号，到期前这么久就重新登录。",
  },
  { key: "relogin_delay_min_seconds", group: "pace", label: "最短间隔", unit: "秒", help: "两次登录之间至少等这么久。" },
  { key: "relogin_delay_max_seconds", group: "pace", label: "最长间隔", unit: "秒", help: "实际在最短和最长之间随机。" },
];

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

function formatDuration(run: PatrolRun) {
  if (!run.started_at || !run.finished_at) {
    return "—";
  }
  const seconds = Math.max(0, (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000);
  if (!Number.isFinite(seconds)) {
    return "—";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes} 分 ${Math.round(seconds % 60)} 秒`;
}

function ResultBadge({ result }: { result: string }) {
  const meta = RESULT_META[result as PatrolResult] ?? { label: result, variant: "secondary" as const };
  return (
    <Badge variant={meta.variant} className="rounded-md whitespace-nowrap">
      {meta.label}
    </Badge>
  );
}

function RunCounts({ run }: { run: PatrolRun }) {
  const counts = run.counts || {};
  const items = RESULT_ORDER.filter((key) => (counts[key] ?? 0) > 0);
  if (items.length === 0) {
    return <span className="text-xs text-stone-400">—</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((key) => (
        <Badge key={key} variant={RESULT_META[key].variant} className="rounded-md whitespace-nowrap">
          {RESULT_META[key].label} {counts[key]}
        </Badge>
      ))}
    </div>
  );
}

function pickToggles(saved: KeeperSettings) {
  return Object.fromEntries(TOGGLE_FIELDS.map((field) => [field.key, Boolean(saved[field.key])])) as Record<ToggleKey, boolean>;
}

function pickDraft(saved: KeeperSettings): Record<string, string> {
  return {
    patrol_time: saved.patrol_time,
    ...Object.fromEntries(NUMBER_FIELDS.map((field) => [field.key, String(saved[field.key])])),
  };
}

function SettingsCard({ saved, onSaved }: { saved: KeeperSettingsResponse; onSaved: (data: KeeperSettingsResponse) => void }) {
  const [toggles, setToggles] = useState(() => pickToggles(saved));
  const [draft, setDraft] = useState(() => pickDraft(saved));
  const [isSaving, setIsSaving] = useState(false);

  const dirty =
    TOGGLE_FIELDS.some((field) => toggles[field.key] !== Boolean(saved[field.key])) ||
    draft.patrol_time !== saved.patrol_time ||
    NUMBER_FIELDS.some((field) => draft[field.key]?.trim() !== String(saved[field.key]));

  const reset = () => {
    setToggles(pickToggles(saved));
    setDraft(pickDraft(saved));
  };

  const handleSave = async () => {
    if (!/^\d{1,2}:\d{2}$/.test(draft.patrol_time || "")) {
      toast.error("请填写巡检时间，格式 HH:MM");
      return;
    }
    const payload: Record<string, unknown> = { ...toggles, patrol_time: draft.patrol_time };
    for (const field of NUMBER_FIELDS) {
      const value = Number(draft[field.key]);
      if (draft[field.key]?.trim() === "" || !Number.isFinite(value) || value < 0) {
        toast.error(`「${field.label}」需要填一个不小于 0 的数字`);
        return;
      }
      payload[field.key] = Math.round(value);
    }
    if (Number(payload.relogin_delay_max_seconds) < Number(payload.relogin_delay_min_seconds)) {
      toast.error("「最长间隔」不能小于「最短间隔」");
      return;
    }
    setIsSaving(true);
    try {
      onSaved(await saveKeeperSettings(payload as Partial<KeeperSettings>));
      toast.success("已保存，立即生效");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存设置失败");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="divide-y divide-stone-100 p-0">
        {GROUPS.map((group) => (
          <div key={group.key} className="grid gap-4 px-6 py-5 md:grid-cols-[220px_1fr] md:gap-8">
            <div>
              <h3 className="text-sm font-semibold text-stone-800">{group.title}</h3>
              <p className="mt-1 text-xs leading-5 text-stone-500">{group.desc}</p>
            </div>
            <div className="max-w-3xl space-y-4">
              {TOGGLE_FIELDS.filter((field) => field.group === group.key).map((field) => (
                <label key={field.key} className="flex cursor-pointer items-start justify-between gap-6">
                  <div>
                    <div className="text-sm font-medium text-stone-700">{field.label}</div>
                    <div className="text-xs leading-5 text-stone-500">{field.help}</div>
                  </div>
                  <Toggle
                    checked={Boolean(toggles[field.key])}
                    onChange={(value) => setToggles((prev) => ({ ...prev, [field.key]: value }))}
                  />
                </label>
              ))}
              <div className="grid gap-4 sm:grid-cols-2">
                {group.key === "schedule" ? (
                  <div className="space-y-1.5">
                    <label className="text-sm font-medium text-stone-700">巡检时间</label>
                    <Input
                      type="time"
                      value={draft.patrol_time || ""}
                      disabled={!toggles.patrol_enabled}
                      onChange={(event) => setDraft((prev) => ({ ...prev, patrol_time: event.target.value }))}
                      className="h-10 rounded-xl border-stone-200 bg-white"
                    />
                    <div className="text-xs text-stone-400">北京时间。</div>
                  </div>
                ) : null}
                {NUMBER_FIELDS.filter((field) => field.group === group.key).map((field) => (
                  <div key={field.key} className="space-y-1.5">
                    <label className="text-sm font-medium text-stone-700">{field.label}</label>
                    <div className="relative">
                      <Input
                        type="number"
                        min={0}
                        value={draft[field.key] ?? ""}
                        onChange={(event) => setDraft((prev) => ({ ...prev, [field.key]: event.target.value }))}
                        className="h-10 rounded-xl border-stone-200 bg-white pr-12"
                      />
                      <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs text-stone-400">
                        {field.unit}
                      </span>
                    </div>
                    <div className="text-xs leading-5 text-stone-400">{field.help}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
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

function RunDetailDialog({
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
  const [detail, setDetail] = useState<PatrolRunDetail | null>(null);
  const [problemsOnly, setProblemsOnly] = useState(false);

  useEffect(() => {
    if (!open || !runId) {
      return;
    }
    let active = true;
    void fetchPatrolRun(runId)
      .then((data) => {
        if (active) {
          setDetail(data);
        }
      })
      .catch((error) => toast.error(error instanceof Error ? error.message : "加载巡检详情失败"));
    return () => {
      active = false;
    };
  }, [open, runId, refreshKey]);

  const current = detail && detail.id === runId ? detail : null;
  const isLoading = open && Boolean(runId) && !current;
  const entries = useMemo(() => {
    const items = current?.entries ?? [];
    return problemsOnly ? items.filter((entry) => PROBLEM_RESULTS.has(entry.result)) : items;
  }, [current, problemsOnly]);
  const problemCount = useMemo(() => (current?.entries ?? []).filter((entry) => PROBLEM_RESULTS.has(entry.result)).length, [current]);

  const errorText = (entry: PatrolEntry) => {
    const parts = [];
    if (entry.error) {
      parts.push(entry.error);
    }
    if (entry.refresh_error && entry.refresh_error !== entry.error) {
      parts.push(`刷新失败：${entry.refresh_error}`);
    }
    return parts.join("；");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(88vh,860px)] w-[min(94vw,1100px)] max-w-none flex-col overflow-hidden rounded-2xl p-0 sm:max-w-none">
        <DialogHeader className="shrink-0 border-b border-stone-100 px-6 py-5">
          <DialogTitle>巡检详情</DialogTitle>
          <DialogDescription className="text-sm leading-6">
            {current
              ? `${formatTime(current.started_at)} · ${TRIGGER_LABELS[current.trigger] || current.trigger} · 已处理 ${current.done}/${current.total}`
              : runId}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-100 px-6 py-3">
          {current ? <RunCounts run={current} /> : <span />}
          <label className="flex cursor-pointer items-center gap-2 text-sm text-stone-600">
            <Checkbox checked={problemsOnly} onCheckedChange={(checked) => setProblemsOnly(Boolean(checked))} />
            只看问题（{problemCount}）
          </label>
        </div>
        <div className="flex-1 overflow-auto">
          {isLoading && !current ? (
            <div className="flex items-center justify-center py-16">
              <LoaderCircle className="size-5 animate-spin text-stone-400" />
            </div>
          ) : current?.error ? (
            <div className="px-6 py-4 text-sm text-rose-600">巡检中断：{current.error}</div>
          ) : null}
          {current ? (
            <Table className="min-w-[960px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-40">时间</TableHead>
                  <TableHead>邮箱 / token</TableHead>
                  <TableHead className="w-32">状态变化</TableHead>
                  <TableHead className="w-36">动作</TableHead>
                  <TableHead className="w-28">结果</TableHead>
                  <TableHead>错误</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entries.map((entry, index) => (
                  <TableRow key={`${entry.token}-${index}`} className="text-stone-600">
                    <TableCell className="text-xs whitespace-nowrap">{formatTime(entry.at)}</TableCell>
                    <TableCell>
                      <div className="text-sm font-medium text-stone-700">{entry.email || "—"}</div>
                      <div className="text-xs text-stone-400">{entry.token}</div>
                    </TableCell>
                    <TableCell className="text-xs whitespace-nowrap">
                      {entry.status_before || "—"}
                      {entry.status_after && entry.status_after !== entry.status_before ? ` → ${entry.status_after}` : ""}
                    </TableCell>
                    <TableCell>
                      {entry.actions?.length ? (
                        <div className="flex flex-wrap gap-1">
                          {entry.actions.map((action) => (
                            <Badge key={action} variant="outline" className="rounded-md border-stone-200 text-stone-600">
                              {ACTION_LABELS[action] || action}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-xs text-stone-400">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <ResultBadge result={entry.result} />
                    </TableCell>
                    <TableCell className="max-w-[360px] text-xs break-all text-rose-600">{errorText(entry) || ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
          {current && entries.length === 0 ? (
            <div className="px-6 py-14 text-center text-sm text-stone-500">
              {problemsOnly ? "这次巡检没有问题账号" : "这次巡检还没有记录"}
            </div>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function PatrolContent() {
  const [runs, setRuns] = useState<PatrolRun[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isStarting, setIsStarting] = useState(false);
  const [detailRunId, setDetailRunId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const timerRef = useRef<number | null>(null);
  const [keeper, setKeeper] = useState<KeeperSettingsResponse | null>(null);

  useEffect(() => {
    let active = true;
    void fetchKeeperSettings()
      .then((data) => {
        if (active) {
          setKeeper(data);
        }
      })
      .catch((error) => toast.error(error instanceof Error ? error.message : "加载巡检设置失败"));
    return () => {
      active = false;
    };
  }, []);

  const loadRuns = useCallback(async (silent = false) => {
    if (!silent) {
      setIsLoading(true);
    }
    try {
      const data = await fetchPatrolRuns();
      setRuns(data.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载巡检记录失败");
    } finally {
      if (!silent) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetchPatrolRuns()
      .then((data) => {
        if (active) {
          setRuns(data.items);
        }
      })
      .catch((error) => toast.error(error instanceof Error ? error.message : "加载巡检记录失败"))
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const hasRunning = runs.some((run) => run.state === "running");

  useEffect(() => {
    if (!hasRunning) {
      return;
    }
    timerRef.current = window.setInterval(() => {
      void loadRuns(true);
      setRefreshKey((value) => value + 1);
    }, 3000);
    return () => {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
      // 巡检刚结束时再拉一次详情，拿到最后几条记录。
      setRefreshKey((value) => value + 1);
    };
  }, [hasRunning, loadRuns]);

  const handleRunNow = async () => {
    setIsStarting(true);
    try {
      const result = await runPatrolNow();
      toast.success(result.id && result.started_at ? "巡检已开始" : "巡检正在启动");
      await loadRuns(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动巡检失败");
    } finally {
      setIsStarting(false);
    }
  };

  const openDetail = (run: PatrolRun) => {
    setDetailRunId(run.id);
    setDetailOpen(true);
  };

  return (
    <section className="space-y-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">巡检</h1>
          <p className="text-sm text-stone-500">
            {keeper === null
              ? "正在加载…"
              : keeper.patrol_enabled
                ? `每天 ${keeper.patrol_time} 自动检查账号，下次 ${formatTime(keeper.next_run_at)}`
                : "每日定时巡检已关闭，可以手动巡检"}
            {keeper && !keeper.auto_relogin_enabled ? " · 掉线后立即重登已关闭" : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => void loadRuns()}
            disabled={isLoading}
            className="h-10 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
          >
            <RefreshCw className={`size-4 ${isLoading ? "animate-spin" : ""}`} />
            刷新
          </Button>
          <Button
            onClick={() => void handleRunNow()}
            disabled={isStarting || hasRunning}
            className="h-10 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
          >
            {isStarting || hasRunning ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}
            {hasRunning ? "巡检进行中" : "立即巡检"}
          </Button>
        </div>
      </div>

      <Tabs defaultValue="runs" className="space-y-4">
        <TabsList variant="line" className="justify-start">
          <TabsTrigger value="runs" className="px-4">
            巡检记录
          </TabsTrigger>
          <TabsTrigger value="settings" className="px-4">
            自动登录设置
          </TabsTrigger>
          <TabsTrigger value="mailbox" className="px-4">
            邮箱与路由
          </TabsTrigger>
        </TabsList>

        <TabsContent value="settings">
          {keeper ? (
            <SettingsCard key={JSON.stringify(keeper)} saved={keeper} onSaved={setKeeper} />
          ) : (
            <div className="flex items-center justify-center py-14">
              <LoaderCircle className="size-5 animate-spin text-stone-400" />
            </div>
          )}
        </TabsContent>

        <TabsContent value="mailbox">
          <MailboxRoutingCard />
        </TabsContent>

        <TabsContent value="runs">
          <Card className="overflow-hidden rounded-2xl border-white/80 bg-white/90 shadow-sm">
            <CardContent className="p-0">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-100 px-5 py-4">
                <div className="flex items-center gap-2 text-sm text-stone-600">
                  <ShieldCheck className="size-4 text-stone-500" />
                  <span>巡检记录 {runs.length} 次</span>
                  {hasRunning ? <span className="text-xs text-stone-400">（每 3 秒自动刷新）</span> : null}
                </div>
                <span className="text-xs text-stone-400">点击某次巡检查看每个账号的处理结果</span>
              </div>
              <div className="overflow-x-auto">
                <Table className="min-w-[900px]">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-44">开始时间</TableHead>
                      <TableHead className="w-20">触发</TableHead>
                      <TableHead className="w-24">状态</TableHead>
                      <TableHead className="w-40">进度</TableHead>
                      <TableHead>结果统计</TableHead>
                      <TableHead className="w-28">耗时</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {runs.map((run) => {
                      const state = STATE_META[run.state] ?? { label: run.state, variant: "secondary" as const };
                      const progress = run.total > 0 ? Math.round((run.done / run.total) * 100) : 0;
                      return (
                        <TableRow key={run.id} className="cursor-pointer text-stone-600" onClick={() => openDetail(run)}>
                          <TableCell className="whitespace-nowrap">{formatTime(run.started_at)}</TableCell>
                          <TableCell>{TRIGGER_LABELS[run.trigger] || run.trigger}</TableCell>
                          <TableCell>
                            <Badge variant={state.variant} className="rounded-md" title={run.error || undefined}>
                              {state.label}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <div className="space-y-1">
                              <div className="text-xs text-stone-500">
                                {run.done}/{run.total}
                              </div>
                              {run.state === "running" ? (
                                <div className="h-1.5 overflow-hidden rounded-full bg-stone-200">
                                  <div className="h-full rounded-full bg-stone-900 transition-all" style={{ width: `${progress}%` }} />
                                </div>
                              ) : null}
                            </div>
                          </TableCell>
                          <TableCell>
                            <RunCounts run={run} />
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-stone-500">
                            {run.state === "running" ? "进行中" : formatDuration(run)}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
              {isLoading && runs.length === 0 ? (
                <div className="flex items-center justify-center py-14">
                  <LoaderCircle className="size-5 animate-spin text-stone-400" />
                </div>
              ) : null}
              {!isLoading && runs.length === 0 ? (
                <div className="px-6 py-14 text-center text-sm text-stone-500">还没有巡检记录，点击「立即巡检」跑一次。</div>
              ) : null}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <RunDetailDialog runId={detailRunId} open={detailOpen} onOpenChange={setDetailOpen} refreshKey={refreshKey} />
    </section>
  );
}

export default function PatrolPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }
  return <PatrolContent />;
}
