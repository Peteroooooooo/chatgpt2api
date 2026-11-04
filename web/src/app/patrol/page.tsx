"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CalendarClock, LoaderCircle, Play, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  fetchKeeperSettings,
  fetchPatrolRun,
  fetchPatrolRuns,
  runPatrolNow,
  saveKeeperSettings,
  type KeeperSettings,
  type PatrolEntry,
  type PatrolResult,
  type PatrolRun,
  type PatrolRunDetail,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

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
  refresh: "刷新令牌",
  relogin: "重新登录",
};

const NUMBER_FIELDS: { key: keyof KeeperSettings; label: string; help: string }[] = [
  { key: "relogin_delay_min_seconds", label: "重登间隔下限（秒）", help: "两次重新登录之间至少等待的秒数" },
  { key: "relogin_delay_max_seconds", label: "重登间隔上限（秒）", help: "实际间隔在上下限之间随机" },
  {
    key: "relogin_when_access_token_expires_within_hours",
    label: "无 RT 提前重登（小时）",
    help: "没有 refresh_token 的账号，access_token 剩余不足这么多小时就重新登录",
  },
  { key: "keep_runs", label: "保留巡检记录（次）", help: "超出后删除最旧的记录" },
];

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

function SettingsCard() {
  const [settings, setSettings] = useState<KeeperSettings | null>(null);
  const [nextRunAt, setNextRunAt] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [enabled, setEnabled] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  const apply = (data: KeeperSettings & { next_run_at: string }) => {
    setSettings(data);
    setNextRunAt(data.next_run_at);
    setEnabled(Boolean(data.patrol_enabled));
    setDraft({
      patrol_time: data.patrol_time,
      ...Object.fromEntries(NUMBER_FIELDS.map((field) => [field.key, String(data[field.key])])),
    });
  };

  useEffect(() => {
    let active = true;
    void fetchKeeperSettings()
      .then((data) => {
        if (active) {
          apply(data);
        }
      })
      .catch((error) => toast.error(error instanceof Error ? error.message : "加载巡检设置失败"))
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const dirty =
    settings !== null &&
    (enabled !== settings.patrol_enabled ||
      draft.patrol_time !== settings.patrol_time ||
      NUMBER_FIELDS.some((field) => draft[field.key]?.trim() !== String(settings[field.key])));

  const handleSave = async () => {
    if (!/^\d{1,2}:\d{2}$/.test(draft.patrol_time || "")) {
      toast.error("请填写巡检时间，格式 HH:MM");
      return;
    }
    const payload: Partial<KeeperSettings> = { patrol_enabled: enabled, patrol_time: draft.patrol_time };
    for (const field of NUMBER_FIELDS) {
      const value = Number(draft[field.key]);
      if (!Number.isFinite(value) || value < 0) {
        toast.error(`${field.label}需要是非负数字`);
        return;
      }
      (payload as Record<string, unknown>)[field.key] = Math.round(value);
    }
    setIsSaving(true);
    try {
      apply(await saveKeeperSettings(payload));
      toast.success("巡检设置已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存巡检设置失败");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="space-y-5 p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-stone-100">
              <CalendarClock className="size-5 text-stone-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold tracking-tight">巡检设置</h2>
              <p className="text-sm text-stone-500">
                每天定时检查所有账号：有 refresh_token 的先刷新，失效的用保存的邮箱密码重新登录。
              </p>
            </div>
          </div>
          {nextRunAt ? (
            <Badge variant={enabled ? "info" : "secondary"} className="rounded-md px-2.5 py-1">
              {settings?.patrol_enabled ? `下次巡检 ${formatTime(nextRunAt)}` : "定时巡检已关闭"}
            </Badge>
          ) : null}
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <LoaderCircle className="size-5 animate-spin text-stone-400" />
          </div>
        ) : (
          <>
            <div className="grid gap-4 md:grid-cols-3">
              <label className="flex cursor-pointer items-start gap-3 rounded-xl bg-stone-50 px-3 py-3">
                <Checkbox checked={enabled} onCheckedChange={(checked) => setEnabled(Boolean(checked))} className="mt-0.5" />
                <div className="space-y-0.5">
                  <div className="text-sm font-medium text-stone-700">启用每日定时巡检</div>
                  <div className="text-xs leading-5 text-stone-500">关闭后仍可手动「立即巡检」。</div>
                </div>
              </label>
              <div className="space-y-2">
                <label className="text-sm font-medium text-stone-700">巡检时间（北京时间）</label>
                <Input
                  type="time"
                  value={draft.patrol_time || ""}
                  onChange={(event) => setDraft((prev) => ({ ...prev, patrol_time: event.target.value }))}
                  className="h-10 rounded-xl border-stone-200 bg-white"
                />
              </div>
              {NUMBER_FIELDS.map((field) => (
                <div key={field.key} className="space-y-2">
                  <label className="text-sm font-medium text-stone-700">{field.label}</label>
                  <Input
                    type="number"
                    min={0}
                    value={draft[field.key] || ""}
                    onChange={(event) => setDraft((prev) => ({ ...prev, [field.key]: event.target.value }))}
                    className="h-10 rounded-xl border-stone-200 bg-white"
                  />
                  <div className="text-xs text-stone-400">{field.help}</div>
                </div>
              ))}
            </div>
            <div className="flex items-center justify-end gap-2">
              {dirty ? <span className="text-xs text-amber-600">有未保存的修改</span> : null}
              <Button
                className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
                onClick={() => void handleSave()}
                disabled={!dirty || isSaving}
              >
                {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
                保存设置
              </Button>
            </div>
          </>
        )}
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
  const problemCount = useMemo(
    () => (current?.entries ?? []).filter((entry) => PROBLEM_RESULTS.has(entry.result)).length,
    [current],
  );

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
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Patrol</div>
          <h1 className="text-2xl font-semibold tracking-tight">巡检日志</h1>
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

      <SettingsCard />

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
