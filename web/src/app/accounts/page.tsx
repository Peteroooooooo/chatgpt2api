"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Box,
  Download,
  Gift,
  KeyRound,
  LoaderCircle,
  LogIn,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  checkPlusTrialEligibility,
  deleteAccounts,
  fetchAccounts,
  fetchModels,
  fetchRefreshProgress,
  fetchReLoginProgress,
  reLoginAccounts,
  refreshAccounts,
  testChatUsability,
  testProxy,
  testRealChat,
  updateAccount,
  type Account,
  type AccountStatus,
  type Model,
  type RefreshProgressResponse,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

import { AccountDrawer } from "./components/account-drawer";
import { AccountImportDialog } from "./components/account-import-dialog";
import { AccountsTable, type RowActions, type RowBusy } from "./components/accounts-table";
import { PasswordImportDialog } from "./components/password-import-dialog";
import { accountType, copyText, downloadTokens, STATUS_ORDER } from "./lib/format";

type Progress = { current: number; total: number; message: string; detail: string } | null;
type Confirm = { title: string; description: string; confirmLabel: string; run: () => Promise<void> } | null;

const errorText = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

function pollProgress(
  fetcher: (id: string) => Promise<RefreshProgressResponse>,
  progressId: string,
  onTick: (p: RefreshProgressResponse) => void,
  intervalMs = 400,
) {
  return new Promise<RefreshProgressResponse>((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const p = await fetcher(progressId);
        if (!p.done) {
          onTick(p);
          return;
        }
        clearInterval(timer);
        if (p.error) reject(new Error(p.error));
        else resolve(p);
      } catch (error) {
        clearInterval(timer);
        reject(error);
      }
    }, intervalMs);
  });
}

function withToken(set: Set<string>, tokens: string[], add: boolean) {
  const next = new Set(set);
  tokens.forEach((token) => (add ? next.add(token) : next.delete(token)));
  return next;
}

function pagerItems(page: number, pageCount: number) {
  const items: (number | "…")[] = [];
  const start = Math.max(1, page - 1);
  const end = Math.min(pageCount, page + 1);
  if (start > 1) items.push(1);
  if (start > 2) items.push("…");
  for (let p = start; p <= end; p += 1) items.push(p);
  if (end < pageCount - 1) items.push("…");
  if (end < pageCount) items.push(pageCount);
  return items;
}

function AccountsPageContent() {
  const didLoadRef = useRef(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<AccountStatus | "">("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState("20");

  const [drawer, setDrawer] = useState<{ token: string; email: string; open: boolean } | null>(null);
  const [busy, setBusy] = useState<RowBusy>({ refreshing: new Set(), chat: new Set(), realChat: new Set(), relogin: new Set() });
  const [progress, setProgress] = useState<Progress>(null);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [checkingPromo, setCheckingPromo] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);
  const [isTestingProxy, setIsTestingProxy] = useState(false);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [passwordProgress, setPasswordProgress] = useState<{ done: number; total: number } | null>(null);
  const [modelsOpen, setModelsOpen] = useState(false);

  const markBusy = (key: keyof RowBusy, tokens: string[], add: boolean) =>
    setBusy((prev) => ({ ...prev, [key]: withToken(prev[key], tokens, add) }));

  const applyItems = (items: Account[]) => {
    setAccounts(items);
    setSelected((prev) => new Set([...prev].filter((token) => items.some((item) => item.access_token === token))));
    // A re-login replaces the access token; keep the drawer on the same account by email.
    setDrawer((prev) => {
      if (!prev || items.some((item) => item.access_token === prev.token)) return prev;
      const same = prev.email ? items.find((item) => item.email === prev.email) : undefined;
      return same ? { ...prev, token: same.access_token } : { ...prev, open: false };
    });
  };

  const loadAccounts = async (silent = false) => {
    if (!silent) setIsLoading(true);
    try {
      applyItems((await fetchAccounts()).items);
    } catch (error) {
      toast.error(errorText(error, "加载账号失败"));
    } finally {
      if (!silent) setIsLoading(false);
    }
  };

  useEffect(() => {
    if (didLoadRef.current) return;
    didLoadRef.current = true;
    void loadAccounts();
    fetchModels()
      .then((data) => setModels(Array.isArray(data.data) ? data.data : []))
      .catch(() => setModels([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const byToken = useMemo(() => new Map(accounts.map((item) => [item.access_token, item])), [accounts]);
  const counts = useMemo(() => {
    const result: Record<string, number> = {};
    accounts.forEach((item) => (result[item.status] = (result[item.status] ?? 0) + 1));
    return result;
  }, [accounts]);
  const quotaTotal = useMemo(
    () => accounts.filter((item) => item.status === "正常").reduce((sum, item) => sum + Math.max(0, item.quota), 0),
    [accounts],
  );
  const typeOptions = useMemo(() => Array.from(new Set(accounts.map(accountType))).sort(), [accounts]);
  const abnormalTokens = useMemo(() => accounts.filter((item) => item.status === "异常").map((item) => item.access_token), [accounts]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return accounts.filter(
      (item) =>
        (!statusFilter || item.status === statusFilter) &&
        (typeFilter === "all" || accountType(item) === typeFilter) &&
        (!q || (item.email ?? "").toLowerCase().includes(q) || item.access_token.toLowerCase().includes(q)),
    );
  }, [accounts, query, statusFilter, typeFilter]);

  const size = Number(pageSize);
  const pageCount = Math.max(1, Math.ceil(filtered.length / size));
  const safePage = Math.min(page, pageCount);
  const start = (safePage - 1) * size;
  const rows = filtered.slice(start, start + size);
  const selectedTokens = [...selected];

  // ── actions ──

  const refresh = async (tokens: string[]) => {
    if (tokens.length === 0) return;
    const single = tokens.length === 1;
    if (single) markBusy("refreshing", tokens, true);
    else {
      setBulkRunning(true);
      setProgress({ current: 0, total: tokens.length, message: "正在刷新账号信息和额度", detail: "" });
    }
    try {
      const { progress_id } = await refreshAccounts(tokens);
      const done = await pollProgress(fetchRefreshProgress, progress_id, (p) =>
        single ? null : setProgress((prev) => prev && { ...prev, current: p.processed }),
      );
      const data = done.result;
      if (!data) throw new Error("刷新结果为空");
      applyItems(data.items);
      const errors = data.errors ?? [];
      if (errors.length > 0) {
        toast.error(
          single ? `刷新失败：${errors[0].error}` : `刷新成功 ${data.refreshed} 个，失败 ${errors.length} 个；首个错误：${errors[0].error}`,
        );
      } else {
        const relogined = data.relogined ?? 0;
        toast.success(single ? "已刷新" : `已刷新 ${data.refreshed} 个账号${relogined ? `，${relogined} 个开始重新登录` : ""}`);
      }
    } catch (error) {
      toast.error(errorText(error, "刷新账号失败"));
    } finally {
      if (single) markBusy("refreshing", tokens, false);
      else {
        setBulkRunning(false);
        setProgress(null);
      }
    }
  };

  const relogin = async (tokens: string[], onlyAbnormal: boolean) => {
    const targets = onlyAbnormal ? tokens.filter((token) => byToken.get(token)?.status === "异常") : tokens;
    if (targets.length === 0) {
      toast.error("选中的账号里没有异常账号");
      return;
    }
    if (targets.length < tokens.length) toast.info(`已跳过 ${tokens.length - targets.length} 个不是异常状态的账号`);
    const single = targets.length === 1;
    markBusy("relogin", targets, true);
    if (!single) {
      setBulkRunning(true);
      setProgress({ current: 0, total: targets.length, message: "正在重新登录", detail: "" });
    } else {
      toast.info("已开始重新登录，可能需要等邮箱验证码，最长约 2 分钟");
    }
    try {
      const { progress_id } = await reLoginAccounts(targets);
      const done = await pollProgress(
        fetchReLoginProgress,
        progress_id,
        (p) => {
          if (single) return;
          const failed = [...(p.results ?? [])].reverse().find((r) => r.error);
          setProgress((prev) => prev && { ...prev, current: p.processed, detail: failed ? `最近失败：${failed.error}` : "" });
        },
        1000,
      );
      await loadAccounts(true);
      const results = done.results ?? [];
      const ok = results.filter((r) => r.status === "成功").length;
      if (single) {
        const result = results[0];
        if (result?.status === "成功") toast.success("重新登录成功");
        else toast.error(`重新登录没有成功：${result?.error || result?.status || "未知原因"}`);
      } else {
        toast[ok === targets.length ? "success" : "warning"](`重新登录完成：成功 ${ok} 个，未成功 ${targets.length - ok} 个`);
      }
    } catch (error) {
      toast.error(errorText(error, "重新登录失败"));
    } finally {
      markBusy("relogin", targets, false);
      if (!single) {
        setBulkRunning(false);
        setProgress(null);
      }
    }
  };

  const runTest = async (tokens: string[], kind: "chat" | "realChat") => {
    if (tokens.length === 0) return;
    const name = kind === "chat" ? "快速测试" : "真实对话测试";
    markBusy(kind, tokens, true);
    try {
      const data = kind === "chat" ? await testChatUsability(tokens) : await testRealChat(tokens);
      applyItems(data.items);
      const usable = data.results.filter((item) => item.status === "可用").length;
      const unusable = data.results.filter((item) => item.status === "不可用").length;
      const failed = data.results.filter((item) => item.status === "测试失败");
      if (tokens.length === 1) {
        const result = data.results[0];
        if (result?.status === "可用") toast.success(`${name}：可用`);
        else toast.warning(`${name}：${result?.status ?? "未知"}${result?.error ? `，${result.error}` : ""}`);
      } else {
        toast[failed.length || unusable ? "warning" : "success"](
          `${name}完成：可用 ${usable}，不可用 ${unusable}，测试失败 ${failed.length}`,
        );
      }
    } catch (error) {
      toast.error(errorText(error, `${name}失败`));
    } finally {
      markBusy(kind, tokens, false);
    }
  };

  const checkPromo = async (tokens: string[]) => {
    if (tokens.length === 0) return;
    setCheckingPromo(true);
    try {
      const data = await checkPlusTrialEligibility(tokens);
      applyItems(data.items);
      const eligible = data.results.filter((item) => item.eligible === true).length;
      const ineligible = data.results.filter((item) => item.eligible === false).length;
      const failed = data.results.filter((item) => item.eligible === null).length;
      toast[failed ? "warning" : "success"](
        `试用资格检测完成：有资格 ${eligible}，无资格 ${ineligible}${failed ? `，检测失败 ${failed}（保留原标识）` : ""}`,
      );
    } catch (error) {
      toast.error(errorText(error, "试用资格检测失败"));
    } finally {
      setCheckingPromo(false);
    }
  };

  const askDelete = (tokens: string[], label: string) => {
    if (tokens.length === 0) return;
    setConfirm({
      title: `删除${label}？`,
      description: `将从号池移除 ${tokens.length} 个账号，删除后无法恢复。账号本身在 ChatGPT 那边不受影响，之后可以重新导入。`,
      confirmLabel: "删除",
      run: async () => {
        setIsDeleting(true);
        try {
          const data = await deleteAccounts(tokens);
          applyItems(data.items);
          toast.success(`已删除 ${data.removed ?? tokens.length} 个账号`);
        } catch (error) {
          toast.error(errorText(error, "删除账号失败"));
        } finally {
          setIsDeleting(false);
        }
      },
    });
  };

  const saveAccount = async (token: string, patch: { status: AccountStatus; proxy: string }) => {
    setIsUpdating(true);
    try {
      applyItems((await updateAccount(token, patch)).items);
      toast.success("已保存");
    } catch (error) {
      toast.error(errorText(error, "保存失败"));
    } finally {
      setIsUpdating(false);
    }
  };

  const checkProxy = async (proxy: string) => {
    setIsTestingProxy(true);
    try {
      const { result } = await testProxy(proxy);
      if (result.ok) toast.success(`代理可用（${result.latency_ms} ms，HTTP ${result.status}）`);
      else toast.error(`代理不可用：${result.error ?? "未知错误"}`);
    } catch (error) {
      toast.error(errorText(error, "测试代理失败"));
    } finally {
      setIsTestingProxy(false);
    }
  };

  const copyToken = (token: string) => {
    void copyText(token).then(
      () => toast.success("Token 已复制"),
      () => toast.error("复制失败，浏览器没有给剪贴板权限"),
    );
  };

  const actions: RowActions = {
    open: (account) => setDrawer({ token: account.access_token, email: account.email ?? "", open: true }),
    quickTest: (token) => void runTest([token], "chat"),
    realTest: (token) => void runTest([token], "realChat"),
    refresh: (token) => void refresh([token]),
    relogin: (token) => void relogin([token], false),
    copyToken,
    remove: (account) => askDelete([account.access_token], ` ${account.email || "这个账号"}`),
  };

  const toggle = (token: string, checked: boolean) => setSelected((prev) => withToken(prev, [token], checked));
  const toggleAll = (checked: boolean) =>
    setSelected((prev) =>
      withToken(
        prev,
        rows.map((row) => row.access_token),
        checked,
      ),
    );
  const setFilter = (fn: () => void) => {
    fn();
    setPage(1);
  };

  const drawerAccount = drawer ? (byToken.get(drawer.token) ?? null) : null;
  const busyAll = isLoading || bulkRunning || isDeleting;
  const tabs: { value: AccountStatus | ""; label: string; count: number }[] = [
    { value: "", label: "全部", count: accounts.length },
    ...STATUS_ORDER.map((status) => ({ value: status, label: status, count: counts[status] ?? 0 })),
  ];

  return (
    <>
      <section className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">号池管理</h1>
          <p className="mt-1 text-sm text-stone-500">
            <b className="font-semibold text-stone-800">{accounts.length}</b> 个账号 · 可用图片额度{" "}
            <b className="font-semibold text-stone-800">{quotaTotal}</b>
            {passwordProgress ? ` · 邮箱登录 / 注册导入中 ${passwordProgress.done}/${passwordProgress.total}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex">
            <Button
              variant="outline"
              className="h-9 rounded-l-lg rounded-r-none border-stone-200 bg-white px-3"
              onClick={() => void loadAccounts()}
              disabled={busyAll}
              title="重新拉取列表"
            >
              <RefreshCw className={cn("size-4", isLoading ? "animate-spin" : "")} />
              刷新
            </Button>
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  className="h-9 w-8 rounded-l-none rounded-r-lg border-l-0 border-stone-200 bg-white px-0"
                  title="更多刷新"
                >
                  <ChevronDown className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem
                  disabled={busyAll || accounts.length === 0}
                  onSelect={() => void refresh(accounts.map((item) => item.access_token))}
                >
                  <RefreshCw />
                  刷新全部账号信息和额度
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={checkingPromo || accounts.length === 0}
                  onSelect={() => void checkPromo(accounts.map((item) => item.access_token))}
                >
                  <Gift />
                  {checkingPromo ? "正在检测试用资格…" : "检测全部试用资格"}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              <Button className="h-9 rounded-lg bg-stone-950 px-3 text-white hover:bg-stone-800" disabled={busyAll}>
                {passwordProgress ? <LoaderCircle className="size-4 animate-spin" /> : <Plus className="size-4" />}
                添加账号
                <ChevronDown className="size-4 opacity-70" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => setImportOpen(true)}>
                <Upload />
                导入 Token / Session 等
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setPasswordOpen(true)}>
                <KeyRound />
                邮箱登录 / 注册导入
                {passwordProgress ? (
                  <span className="ml-auto text-xs text-stone-400">
                    {passwordProgress.done}/{passwordProgress.total}
                  </span>
                ) : null}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon" className="size-9 rounded-lg border-stone-200 bg-white" title="更多">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem disabled={accounts.length === 0} onSelect={() => downloadTokens(accounts)}>
                <Download />
                导出全部 Token
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setModelsOpen(true)}>
                <Box />
                查看可用模型
                <span className="ml-auto text-xs text-stone-400">{models.length}</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </section>

      {progress ? (
        <div className="rounded-xl border border-stone-200 bg-white px-4 py-3 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="text-stone-600">
              {progress.message}
              {progress.detail ? <span className="ml-2 text-xs text-rose-600 dark:text-rose-400">{progress.detail}</span> : null}
            </span>
            <span className="font-medium tabular-nums text-stone-700">
              {progress.current}/{progress.total}
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-stone-100">
            <div
              className="h-full rounded-full bg-stone-800 transition-all duration-300 dark:bg-stone-200"
              style={{ width: `${progress.total ? (progress.current / progress.total) * 100 : 0}%` }}
            />
          </div>
        </div>
      ) : null}

      <section className="overflow-hidden rounded-xl border border-stone-200 bg-white shadow-sm">
        {selected.size > 0 ? (
          <div className="flex min-h-[60px] flex-wrap items-center gap-2 border-b border-stone-200 bg-stone-50 px-4 py-3">
            <span className="mr-2 text-sm font-semibold text-stone-800">已选 {selected.size} 个账号</span>
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-lg border-stone-200 bg-white"
              disabled={bulkRunning}
              onClick={() => void refresh(selectedTokens)}
            >
              <RefreshCw className="size-4" />
              刷新信息
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-lg border-stone-200 bg-white"
              disabled={busy.chat.size > 0}
              onClick={() => void runTest(selectedTokens, "chat")}
            >
              快速测试
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-lg border-stone-200 bg-white"
              disabled={bulkRunning}
              onClick={() => void relogin(selectedTokens, true)}
              title="只对异常账号重新登录"
            >
              <LogIn className="size-4" />
              尝试恢复
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-lg border-stone-200 bg-white text-rose-600 hover:text-rose-700 dark:text-rose-400"
              disabled={isDeleting}
              onClick={() => askDelete(selectedTokens, "选中的账号")}
            >
              <Trash2 className="size-4" />
              删除
            </Button>
            <div className="flex-1" />
            <Button variant="ghost" size="sm" className="h-8 rounded-lg" onClick={() => setSelected(new Set())}>
              取消选择
            </Button>
          </div>
        ) : (
          <div className="flex min-h-[60px] flex-wrap items-center gap-3 border-b border-stone-200 px-4 py-3">
            <div className="flex max-w-full gap-0.5 overflow-x-auto rounded-lg border border-stone-100 bg-stone-50 p-[3px]">
              {tabs.map((tab) => (
                <button
                  key={tab.value || "all"}
                  type="button"
                  onClick={() => setFilter(() => setStatusFilter(tab.value))}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm whitespace-nowrap text-stone-500 transition hover:text-stone-800",
                    statusFilter === tab.value && "bg-white font-medium text-stone-900 shadow-sm",
                  )}
                >
                  {tab.label}
                  <span
                    className={cn(
                      "text-xs tabular-nums text-stone-400",
                      tab.value === "异常" && tab.count > 0 && "font-semibold text-rose-600 dark:text-rose-400",
                    )}
                  >
                    {tab.count}
                  </span>
                </button>
              ))}
            </div>
            <div className="hidden flex-1 md:block" />
            <div className="relative w-full md:w-64">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-stone-400" />
              <Input
                value={query}
                onChange={(event) => setFilter(() => setQuery(event.target.value))}
                placeholder="搜索邮箱或 Token"
                className="h-9 rounded-lg border-stone-200 bg-white pl-8"
              />
            </div>
            <Select value={typeFilter} onValueChange={(value) => setFilter(() => setTypeFilter(value))}>
              <SelectTrigger className="h-9 w-[120px] rounded-lg border-stone-200 bg-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部类型</SelectItem>
                {typeOptions.map((type) => (
                  <SelectItem key={type} value={type}>
                    {type.toUpperCase()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        {abnormalTokens.length > 0 && selected.size === 0 ? (
          <div className="flex flex-wrap items-center gap-2.5 border-b border-stone-200 bg-rose-50 px-4 py-2.5 text-sm dark:bg-rose-500/10">
            <CircleAlert className="size-4 text-rose-600 dark:text-rose-400" />
            <span className="flex-1 text-stone-700">{abnormalTokens.length} 个账号异常，自动重登没有成功。点开账号可以看到原因。</span>
            <Button
              variant="outline"
              size="sm"
              className="h-7 rounded-lg border-stone-200 bg-white"
              disabled={bulkRunning}
              onClick={() => void relogin(abnormalTokens, true)}
            >
              全部尝试恢复
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 rounded-lg border-stone-200 bg-white text-rose-600 dark:text-rose-400"
              disabled={isDeleting}
              onClick={() => askDelete(abnormalTokens, "全部异常账号")}
            >
              全部移除
            </Button>
          </div>
        ) : null}

        {isLoading && accounts.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-stone-500">
            <LoaderCircle className="size-4 animate-spin" />
            正在加载账号
          </div>
        ) : (
          <>
            <AccountsTable rows={rows} selected={selected} busy={busy} actions={actions} onToggle={toggle} onToggleAll={toggleAll} />
            {rows.length === 0 ? (
              <div className="py-14 text-center text-sm text-stone-500">
                {accounts.length === 0 ? "号池里还没有账号。点右上角「添加账号」导入。" : "没有匹配的账号。换个筛选条件试试。"}
              </div>
            ) : null}
          </>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 px-4 py-2.5 text-sm text-stone-500">
          <span>{filtered.length ? `第 ${start + 1}–${start + rows.length} 个，共 ${filtered.length} 个` : "共 0 个"}</span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-lg"
              disabled={safePage <= 1}
              onClick={() => setPage(safePage - 1)}
            >
              <ChevronLeft className="size-4" />
            </Button>
            {pagerItems(safePage, pageCount).map((item, index) =>
              item === "…" ? (
                <span key={`gap-${index}`} className="px-1 text-stone-400">
                  …
                </span>
              ) : (
                <button
                  key={item}
                  type="button"
                  onClick={() => setPage(item)}
                  className={cn(
                    "h-8 min-w-8 rounded-lg px-2 tabular-nums hover:bg-stone-100",
                    item === safePage && "border border-stone-200 bg-white font-semibold text-stone-900 shadow-sm",
                  )}
                >
                  {item}
                </button>
              ),
            )}
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-lg"
              disabled={safePage >= pageCount}
              onClick={() => setPage(safePage + 1)}
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
          <Select value={pageSize} onValueChange={(value) => setFilter(() => setPageSize(value))}>
            <SelectTrigger className="h-8 w-[100px] rounded-lg border-stone-200 bg-white text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {["10", "20", "50", "100"].map((value) => (
                <SelectItem key={value} value={value}>
                  {value} / 页
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </section>

      <AccountDrawer
        account={drawerAccount}
        open={Boolean(drawer?.open)}
        onClose={() => setDrawer((prev) => prev && { ...prev, open: false })}
        busy={busy}
        actions={actions}
        saving={isUpdating}
        onSave={(token, patch) => void saveAccount(token, patch)}
        testingProxy={isTestingProxy}
        onTestProxy={(proxy) => void checkProxy(proxy)}
      />

      <AccountImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        disabled={busyAll}
        onImported={(items) => {
          applyItems(items);
          setSelected(new Set());
          setPage(1);
        }}
      />
      <PasswordImportDialog
        open={passwordOpen}
        onOpenChange={setPasswordOpen}
        disabled={busyAll}
        onProgress={setPasswordProgress}
        onFinished={() => void loadAccounts(true)}
      />

      <Dialog open={modelsOpen} onOpenChange={setModelsOpen}>
        <DialogContent className="rounded-2xl p-6">
          <DialogHeader>
            <DialogTitle>系统可用模型</DialogTitle>
            <DialogDescription>点一下复制模型名。</DialogDescription>
          </DialogHeader>
          <div className="flex max-h-[50vh] flex-wrap gap-1.5 overflow-y-auto">
            {models.length === 0 ? <span className="text-sm text-stone-400">当前没有可用模型</span> : null}
            {models.map((model) => (
              <button
                key={model.id}
                type="button"
                className="rounded-md border border-stone-200 bg-stone-50 px-2 py-1 font-mono text-xs text-stone-700 hover:bg-stone-100"
                onClick={() => void copyText(model.id).then(() => toast.success(`已复制 ${model.id}`))}
              >
                {model.id}
              </button>
            ))}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(confirm)} onOpenChange={(open) => (!open ? setConfirm(null) : null)}>
        <DialogContent showCloseButton={false} className="rounded-2xl p-6">
          <DialogHeader>
            <DialogTitle>{confirm?.title}</DialogTitle>
            <DialogDescription className="leading-6">{confirm?.description}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="rounded-lg" onClick={() => setConfirm(null)}>
              取消
            </Button>
            <Button
              className="rounded-lg bg-rose-600 text-white hover:bg-rose-700"
              disabled={isDeleting}
              onClick={() => {
                const run = confirm?.run;
                setConfirm(null);
                void run?.();
              }}
            >
              {confirm?.confirmLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function AccountsPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);

  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <AccountsPageContent />;
}
