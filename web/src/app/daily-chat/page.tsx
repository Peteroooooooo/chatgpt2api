"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CalendarClock,
  ExternalLink,
  LoaderCircle,
  RefreshCw,
  Save,
  Send,
  Shuffle,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  fetchDailyChat,
  fetchDailyChatDay,
  rescheduleDailyChat,
  runDailyChatItem,
  saveDailyChatSettings,
  type DailyChatItem,
  type DailyChatOverview,
  type DailyChatPlan,
  type DailyChatSettings,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type BadgeVariant = "success" | "info" | "danger" | "warning" | "secondary";

const CN_TIME: Intl.DateTimeFormatOptions = {
  timeZone: "Asia/Shanghai",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
};

function hhmm(value?: string | null) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("zh-CN", CN_TIME);
}

function resultVariant(result?: string): BadgeVariant {
  if (result === "可用") {
    return "success";
  }
  if (result === "限流") {
    return "warning";
  }
  return "danger";
}

function StateBadge({ item }: { item: DailyChatItem }) {
  if (item.state === "pending") {
    return (
      <Badge variant="secondary" className="rounded-md whitespace-nowrap">
        等待
      </Badge>
    );
  }
  if (item.state === "running") {
    return (
      <Badge variant="info" className="gap-1 rounded-md whitespace-nowrap">
        <LoaderCircle className="size-3 animate-spin" />
        发送中
      </Badge>
    );
  }
  if (item.state === "skipped") {
    return (
      <Badge
        variant="outline"
        className="rounded-md border-stone-200 whitespace-nowrap text-stone-500"
      >
        跳过
      </Badge>
    );
  }
  return (
    <Badge
      variant={resultVariant(item.result)}
      className="rounded-md whitespace-nowrap"
    >
      {item.result || "已发送"}
    </Badge>
  );
}

type CountChip = {
  key: string;
  label: string;
  variant: BadgeVariant;
  value: number;
};

function summarize(counts: Record<string, number>): CountChip[] {
  const chips: CountChip[] = [];
  let failed = 0;
  for (const [key, value] of Object.entries(counts)) {
    if (key === "done:可用") {
      chips.push({ key, label: "成功", variant: "success", value });
    } else if (key.startsWith("done:")) {
      failed += value;
    }
  }
  if (failed) {
    chips.push({
      key: "failed",
      label: "失败",
      variant: "danger",
      value: failed,
    });
  }
  if (counts.running) {
    chips.push({
      key: "running",
      label: "发送中",
      variant: "info",
      value: counts.running,
    });
  }
  if (counts.pending) {
    chips.push({
      key: "pending",
      label: "等待",
      variant: "secondary",
      value: counts.pending,
    });
  }
  if (counts.skipped) {
    chips.push({
      key: "skipped",
      label: "跳过",
      variant: "warning",
      value: counts.skipped,
    });
  }
  return chips;
}

function Counts({ counts }: { counts: Record<string, number> }) {
  const chips = summarize(counts);
  if (chips.length === 0) {
    return <span className="text-xs text-stone-400">—</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {chips.map((chip) => (
        <Badge
          key={chip.key}
          variant={chip.variant}
          className="rounded-md whitespace-nowrap"
        >
          {chip.label} {chip.value}
        </Badge>
      ))}
    </div>
  );
}

function detailText(item: DailyChatItem) {
  if (item.state === "skipped") {
    return item.reason || "";
  }
  if (item.state === "done") {
    const parts = [
      item.error,
      item.duration_seconds != null ? `${item.duration_seconds} 秒` : "",
      item.trigger === "manual" ? "手动发送" : "",
    ];
    return parts.filter(Boolean).join(" · ");
  }
  return "";
}

function ItemsTable({
  items,
  onRun,
  runningIds,
}: {
  items: DailyChatItem[];
  onRun?: (item: DailyChatItem) => void;
  runningIds?: Set<string>;
}) {
  return (
    <div className="overflow-x-auto">
      <Table className="min-w-[860px]">
        <TableHeader>
          <TableRow>
            <TableHead className="w-24">时间</TableHead>
            <TableHead className="w-64">账号</TableHead>
            <TableHead className="w-24">状态</TableHead>
            <TableHead>发送的问题</TableHead>
            <TableHead className="w-56">说明</TableHead>
            <TableHead className="w-28 text-right" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => {
            const moved =
              item.rescheduled &&
              item.planned_at &&
              item.planned_at !== item.scheduled_at;
            return (
              <TableRow key={item.id} className="text-stone-600">
                <TableCell className="whitespace-nowrap tabular-nums">
                  <div className="font-medium text-stone-800">
                    {hhmm(
                      item.state === "done" && item.started_at
                        ? item.started_at
                        : item.scheduled_at,
                    )}
                  </div>
                  {moved ? (
                    <div className="text-xs text-stone-400">
                      原定 {hhmm(item.planned_at)}
                    </div>
                  ) : null}
                </TableCell>
                <TableCell
                  className="max-w-64 truncate font-medium text-stone-700"
                  title={item.label}
                >
                  {item.label}
                </TableCell>
                <TableCell>
                  <StateBadge item={item} />
                </TableCell>
                <TableCell
                  className="max-w-[340px] truncate text-sm"
                  title={item.prompt || undefined}
                >
                  {item.prompt || <span className="text-stone-300">—</span>}
                </TableCell>
                <TableCell
                  className={`max-w-56 truncate text-xs ${item.state === "done" && item.result !== "可用" ? "text-rose-600" : "text-stone-500"}`}
                  title={detailText(item) || undefined}
                >
                  {detailText(item)}
                </TableCell>
                <TableCell className="text-right">
                  {item.state === "pending" && onRun ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={runningIds?.has(item.id)}
                      onClick={() => onRun(item)}
                      className="h-8 rounded-lg border-stone-200 px-2.5 text-stone-700"
                    >
                      <Send className="size-3.5" />
                      立即发送
                    </Button>
                  ) : item.conversation_url ? (
                    <a
                      href={item.conversation_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-stone-500 hover:text-stone-800"
                    >
                      打开对话
                      <ExternalLink className="size-3.5" />
                    </a>
                  ) : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
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

type Draft = {
  window_start: string;
  window_end: string;
  min_gap_minutes: string;
  keep_days: string;
};

function pickDraft(saved: DailyChatSettings): Draft {
  return {
    window_start: saved.window_start,
    window_end: saved.window_end,
    min_gap_minutes: String(saved.min_gap_minutes),
    keep_days: String(saved.keep_days),
  };
}

function SettingsCard({
  saved,
  onSaved,
}: {
  saved: DailyChatSettings;
  onSaved: (data: DailyChatOverview) => void;
}) {
  const [enabled, setEnabled] = useState(saved.enabled);
  const [includeLimited, setIncludeLimited] = useState(saved.include_limited);
  const [draft, setDraft] = useState(() => pickDraft(saved));
  const [isSaving, setIsSaving] = useState(false);

  const dirty =
    enabled !== saved.enabled ||
    includeLimited !== saved.include_limited ||
    (Object.keys(draft) as (keyof Draft)[]).some(
      (key) => draft[key].trim() !== String(saved[key]),
    );

  const reset = () => {
    setEnabled(saved.enabled);
    setIncludeLimited(saved.include_limited);
    setDraft(pickDraft(saved));
  };

  const handleSave = async () => {
    if (
      !/^\d{2}:\d{2}$/.test(draft.window_start) ||
      !/^\d{2}:\d{2}$/.test(draft.window_end)
    ) {
      toast.error("请填写开始和结束时间，格式 HH:MM");
      return;
    }
    if (draft.window_end <= draft.window_start) {
      toast.error("结束时间必须晚于开始时间");
      return;
    }
    const gap = Number(draft.min_gap_minutes);
    const keep = Number(draft.keep_days);
    if (!Number.isFinite(gap) || gap < 0 || gap > 180) {
      toast.error("「最短间隔」填 0 到 180 分钟");
      return;
    }
    if (!Number.isFinite(keep) || keep < 1 || keep > 365) {
      toast.error("「保留记录」填 1 到 365 天");
      return;
    }
    setIsSaving(true);
    try {
      onSaved(
        await saveDailyChatSettings({
          enabled,
          include_limited: includeLimited,
          window_start: draft.window_start,
          window_end: draft.window_end,
          min_gap_minutes: Math.round(gap),
          keep_days: Math.round(keep),
        }),
      );
      toast.success(
        "已保存。明天的计划按新设置排；想让今天也按新设置，点「重新安排今天」",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存设置失败");
    } finally {
      setIsSaving(false);
    }
  };

  const numberField = (
    key: "min_gap_minutes" | "keep_days",
    label: string,
    unit: string,
    help: string,
  ) => (
    <div className="space-y-1.5">
      <label className="text-sm font-medium text-stone-700">{label}</label>
      <div className="relative">
        <Input
          type="number"
          min={0}
          value={draft[key]}
          onChange={(event) =>
            setDraft((prev) => ({ ...prev, [key]: event.target.value }))
          }
          className="h-10 rounded-xl border-stone-200 bg-white pr-12"
        />
        <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs text-stone-400">
          {unit}
        </span>
      </div>
      <div className="text-xs leading-5 text-stone-400">{help}</div>
    </div>
  );

  const timeField = (key: "window_start" | "window_end", label: string) => (
    <div className="space-y-1.5">
      <label className="text-sm font-medium text-stone-700">{label}</label>
      <Input
        type="time"
        value={draft[key]}
        onChange={(event) =>
          setDraft((prev) => ({ ...prev, [key]: event.target.value }))
        }
        className="h-10 rounded-xl border-stone-200 bg-white"
      />
      <div className="text-xs text-stone-400">北京时间。</div>
    </div>
  );

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="divide-y divide-stone-100 p-0">
        <div className="grid gap-4 px-6 py-5 md:grid-cols-[220px_1fr] md:gap-8">
          <div>
            <h3 className="text-sm font-semibold text-stone-800">发送</h3>
            <p className="mt-1 text-xs leading-5 text-stone-500">
              每个账号每天发一条真实对话，问题从题库随机组合。
            </p>
          </div>
          <div className="max-w-3xl space-y-4">
            <label className="flex cursor-pointer items-start justify-between gap-6">
              <div>
                <div className="text-sm font-medium text-stone-700">
                  每天自动发送
                </div>
                <div className="text-xs leading-5 text-stone-500">
                  关闭后不再排计划，也不发送；今天已排好的项目暂停。
                </div>
              </div>
              <Toggle checked={enabled} onChange={setEnabled} />
            </label>
            <label className="flex cursor-pointer items-start justify-between gap-6">
              <div>
                <div className="text-sm font-medium text-stone-700">
                  限流账号也发送
                </div>
                <div className="text-xs leading-5 text-stone-500">
                  限流只影响生图额度，聊天通常还能用。禁用、异常、已停止自动重登的账号总是跳过。
                </div>
              </div>
              <Toggle checked={includeLimited} onChange={setIncludeLimited} />
            </label>
          </div>
        </div>
        <div className="grid gap-4 px-6 py-5 md:grid-cols-[220px_1fr] md:gap-8">
          <div>
            <h3 className="text-sm font-semibold text-stone-800">时间</h3>
            <p className="mt-1 text-xs leading-5 text-stone-500">
              在时间段里给每个账号随机挑一个时间，相邻两个账号至少隔开最短间隔。
            </p>
          </div>
          <div className="grid max-w-3xl gap-4 sm:grid-cols-2">
            {timeField("window_start", "开始时间")}
            {timeField("window_end", "结束时间")}
            {numberField(
              "min_gap_minutes",
              "最短间隔",
              "分钟",
              "间隔不会自动缩短。时间段放不下所有账号时，排不下的当天跳过。",
            )}
            {numberField(
              "keep_days",
              "保留记录",
              "天",
              "更早的每日记录会被删除。",
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 px-6 py-4">
          <span
            className={`mr-auto text-xs ${dirty ? "text-amber-600" : "text-stone-400"}`}
          >
            {dirty ? "有未保存的修改" : "保存后立即生效，不用重启"}
          </span>
          {dirty ? (
            <Button
              variant="ghost"
              className="h-9 rounded-xl px-3 text-stone-600"
              onClick={reset}
              disabled={isSaving}
            >
              <Undo2 className="size-4" />
              撤销
            </Button>
          ) : null}
          <Button
            className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
            onClick={() => void handleSave()}
            disabled={!dirty || isSaving}
          >
            {isSaving ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            保存
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function DayDialog({
  day,
  onOpenChange,
}: {
  day: string | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [plan, setPlan] = useState<DailyChatPlan | null>(null);

  useEffect(() => {
    if (!day) {
      return;
    }
    let active = true;
    void fetchDailyChatDay(day)
      .then((data) => {
        if (active) {
          setPlan(data);
        }
      })
      .catch((error) =>
        toast.error(error instanceof Error ? error.message : "加载记录失败"),
      );
    return () => {
      active = false;
    };
  }, [day]);

  const current = plan && plan.date === day ? plan : null;
  return (
    <Dialog open={Boolean(day)} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(88vh,860px)] w-[min(94vw,1100px)] max-w-none flex-col overflow-hidden rounded-2xl p-0 sm:max-w-none">
        <DialogHeader className="shrink-0 border-b border-stone-100 px-6 py-5">
          <DialogTitle>{day} 的每日对话</DialogTitle>
          <DialogDescription className="text-sm leading-6">
            {current
              ? `时间段 ${current.window || "—"} · 共 ${current.items.length} 个账号`
              : "正在加载…"}
          </DialogDescription>
        </DialogHeader>
        <div className="border-b border-stone-100 px-6 py-3">
          {current ? <Counts counts={current.counts || {}} /> : null}
        </div>
        <div className="flex-1 overflow-auto">
          {current ? (
            <ItemsTable items={current.items} />
          ) : (
            <div className="flex items-center justify-center py-16">
              <LoaderCircle className="size-5 animate-spin text-stone-400" />
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function DailyChatContent() {
  const [overview, setOverview] = useState<DailyChatOverview | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRescheduling, setIsRescheduling] = useState(false);
  const [runningIds, setRunningIds] = useState<Set<string>>(new Set());
  const [openDay, setOpenDay] = useState<string | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) {
      setIsLoading(true);
    }
    try {
      setOverview(await fetchDailyChat());
    } catch (error) {
      if (!silent) {
        toast.error(
          error instanceof Error ? error.message : "加载每日对话失败",
        );
      }
    } finally {
      if (!silent) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetchDailyChat()
      .then((data) => {
        if (active) {
          setOverview(data);
        }
      })
      .catch((error) =>
        toast.error(
          error instanceof Error ? error.message : "加载每日对话失败",
        ),
      )
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const items = overview?.today?.items ?? [];
  const hasRunning = items.some((item) => item.state === "running");
  const waitingForPlan = Boolean(overview?.settings.enabled && !overview.today);

  useEffect(() => {
    const timer = window.setInterval(
      () => void load(true),
      hasRunning || waitingForPlan ? 5000 : 30000,
    );
    return () => window.clearInterval(timer);
  }, [hasRunning, waitingForPlan, load]);

  const handleReschedule = async () => {
    setIsRescheduling(true);
    try {
      setOverview(await rescheduleDailyChat());
      toast.success("已重新安排今天还没发送的账号");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "重新安排失败");
    } finally {
      setIsRescheduling(false);
    }
  };

  const handleRun = async (item: DailyChatItem) => {
    setRunningIds((prev) => new Set(prev).add(item.id));
    try {
      await runDailyChatItem(item.id);
      toast.success(`已开始给 ${item.label} 发送`);
      await load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "发送失败");
    } finally {
      setRunningIds((prev) => {
        const next = new Set(prev);
        next.delete(item.id);
        return next;
      });
    }
  };

  const settings = overview?.settings;
  const today = overview?.today;
  const nextItem = items.find(
    (item) =>
      item.state === "pending" && item.scheduled_at === overview?.next_item_at,
  );

  let subtitle = "正在加载…";
  if (settings && !settings.enabled) {
    subtitle = "每日自动发送已关闭";
  } else if (settings) {
    subtitle = `每天 ${settings.window_start}–${settings.window_end} 给每个账号随机发一条真实对话，问题和「真实对话测试」用同一个题库。`;
    subtitle += nextItem
      ? ` 下一条 ${hhmm(nextItem.scheduled_at)}（${nextItem.label}）`
      : today
        ? " 今天已全部处理完。"
        : "";
  }

  return (
    <section className="space-y-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">每日对话</h1>
          <p className="text-sm text-stone-500">{subtitle}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => void load()}
            disabled={isLoading}
            className="h-10 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
          >
            <RefreshCw
              className={`size-4 ${isLoading ? "animate-spin" : ""}`}
            />
            刷新
          </Button>
          <Button
            onClick={() => void handleReschedule()}
            disabled={isRescheduling || !settings?.enabled}
            title="给今天还没发送的账号重新随机挑时间，并加入今天新加的账号。已发送的不会再发。"
            className="h-10 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
          >
            {isRescheduling ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Shuffle className="size-4" />
            )}
            重新安排今天
          </Button>
        </div>
      </div>

      <Tabs defaultValue="today" className="space-y-4">
        <TabsList variant="line" className="justify-start">
          <TabsTrigger value="today" className="px-4">
            今天
          </TabsTrigger>
          <TabsTrigger value="history" className="px-4">
            历史记录
          </TabsTrigger>
          <TabsTrigger value="settings" className="px-4">
            设置
          </TabsTrigger>
        </TabsList>

        <TabsContent value="today">
          <Card className="overflow-hidden rounded-2xl border-white/80 bg-white/90 shadow-sm">
            <CardContent className="p-0">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-100 px-5 py-4">
                <div className="flex flex-wrap items-center gap-3 text-sm text-stone-600">
                  <CalendarClock className="size-4 text-stone-500" />
                  <span>
                    {today ? `${today.date} · ${items.length} 个账号` : "今天"}
                  </span>
                  <Counts counts={overview?.counts ?? {}} />
                </div>
                <span className="text-xs text-stone-400">
                  每个账号一天只发一次，失败当天不重试；相邻账号至少隔{" "}
                  {settings?.min_gap_minutes ?? "—"} 分钟
                </span>
              </div>
              {today && items.length > 0 ? (
                <ItemsTable
                  items={items}
                  onRun={(item) => void handleRun(item)}
                  runningIds={runningIds}
                />
              ) : null}
              {isLoading && !overview ? (
                <div className="flex items-center justify-center py-14">
                  <LoaderCircle className="size-5 animate-spin text-stone-400" />
                </div>
              ) : null}
              {overview && !today ? (
                <div className="px-6 py-14 text-center text-sm text-stone-500">
                  {settings?.enabled
                    ? "正在排今天的计划，几秒后自动刷新…"
                    : "每日自动发送已关闭，在「设置」里打开后会自动排今天的计划。"}
                </div>
              ) : null}
              {today && items.length === 0 ? (
                <div className="px-6 py-14 text-center text-sm text-stone-500">
                  号池里还没有账号。
                </div>
              ) : null}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="history">
          <Card className="overflow-hidden rounded-2xl border-white/80 bg-white/90 shadow-sm">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-40">日期</TableHead>
                    <TableHead>结果统计</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(overview?.days ?? []).map((day) => (
                    <TableRow
                      key={day.date}
                      className="cursor-pointer text-stone-600"
                      onClick={() => setOpenDay(day.date)}
                    >
                      <TableCell className="whitespace-nowrap tabular-nums">
                        {day.date}
                      </TableCell>
                      <TableCell>
                        <Counts counts={day.counts} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {overview && overview.days.length === 0 ? (
                <div className="px-6 py-14 text-center text-sm text-stone-500">
                  还没有记录。
                </div>
              ) : null}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="settings">
          {settings ? (
            <SettingsCard
              key={JSON.stringify(settings)}
              saved={settings}
              onSaved={setOverview}
            />
          ) : (
            <div className="flex items-center justify-center py-14">
              <LoaderCircle className="size-5 animate-spin text-stone-400" />
            </div>
          )}
        </TabsContent>
      </Tabs>

      <DayDialog
        day={openDay}
        onOpenChange={(open) => (open ? null : setOpenDay(null))}
      />
    </section>
  );
}

export default function DailyChatPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }
  return <DailyChatContent />;
}
