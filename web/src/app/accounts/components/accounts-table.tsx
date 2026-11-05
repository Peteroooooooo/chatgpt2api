"use client";

import { CircleAlert, Copy, Gift, KeyRound, LogIn, MessageCircle, MoreHorizontal, PanelRight, Play, RefreshCw, Trash2 } from "lucide-react";

import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Account } from "@/lib/api";
import { cn } from "@/lib/utils";

import { accountType, ago, shortToken, statusTone, testChipMeta, typeTone, until, type TestStatus } from "../lib/format";

export type RowBusy = {
  refreshing: Set<string>;
  chat: Set<string>;
  realChat: Set<string>;
  relogin: Set<string>;
};

export type RowActions = {
  open: (account: Account) => void;
  quickTest: (token: string) => void;
  realTest: (token: string) => void;
  refresh: (token: string) => void;
  relogin: (token: string) => void;
  copyToken: (token: string) => void;
  remove: (account: Account) => void;
};

type AccountsTableProps = {
  rows: Account[];
  selected: Set<string>;
  busy: RowBusy;
  actions: RowActions;
  onToggle: (token: string, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
};

export function TestChip({
  label,
  status,
  checkedAt,
  error,
  running,
  onRun,
  disabled,
}: {
  label: string;
  status?: TestStatus;
  checkedAt?: string | null;
  error?: string | null;
  running?: boolean;
  onRun?: () => void;
  disabled?: boolean;
}) {
  const meta = testChipMeta(status, Boolean(running));
  const when = status && status !== "未测试" && checkedAt ? ` · ${ago(checkedAt)}` : "";
  const title = `${label}测试：${meta.text}${when}${error ? `\n${error}` : ""}${onRun && !running ? "\n点击运行" : ""}`;
  const className =
    "inline-flex h-[22px] items-center gap-1 rounded-md border border-stone-200 bg-white px-1.5 text-xs whitespace-nowrap text-stone-600";
  const content = (
    <>
      <span className={cn("size-1.5 rounded-full", meta.dot)} />
      {label.slice(0, 1)} {meta.text}
    </>
  );
  if (!onRun) {
    return (
      <span title={title} className={className}>
        {content}
      </span>
    );
  }
  return (
    <button
      type="button"
      title={title}
      disabled={disabled || running}
      onClick={(event) => {
        event.stopPropagation();
        onRun();
      }}
      className={cn(
        className,
        "cursor-pointer transition hover:border-stone-300 hover:bg-stone-50 dark:hover:bg-white/10 disabled:cursor-default disabled:hover:bg-white",
      )}
    >
      {content}
    </button>
  );
}

export function StatusPill({ status }: { status: Account["status"] }) {
  return (
    <span
      className={cn(
        "inline-flex h-[22px] items-center gap-1.5 rounded-full px-2 text-xs font-medium whitespace-nowrap",
        statusTone[status],
      )}
    >
      <span className="size-1.5 rounded-full bg-current" />
      {status}
    </span>
  );
}

function QuotaCell({ account }: { account: Account }) {
  if (account.status !== "正常" && account.status !== "限流") {
    return <span className="text-stone-400">—</span>;
  }
  const quota = account.status === "限流" ? 0 : Math.max(0, account.quota);
  const restore = until(account.restore_at);
  return (
    <div className="flex flex-col leading-tight">
      <span className="font-semibold tabular-nums text-stone-800">{quota}</span>
      {restore ? <span className="text-xs text-stone-400">{restore}</span> : null}
    </div>
  );
}

function RowMenu({ account, busy, actions }: { account: Account; busy: RowBusy; actions: RowActions }) {
  const token = account.access_token;
  const testing = busy.chat.has(token) || busy.realChat.has(token);
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title="操作"
          onClick={(event) => event.stopPropagation()}
          className="inline-flex size-7 items-center justify-center rounded-lg text-stone-400 transition hover:bg-stone-100 hover:text-stone-700"
        >
          <MoreHorizontal className="size-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent onClick={(event) => event.stopPropagation()}>
        <DropdownMenuItem disabled={testing} onSelect={() => actions.quickTest(token)}>
          <Play />
          快速测试
        </DropdownMenuItem>
        <DropdownMenuItem disabled={testing} onSelect={() => actions.realTest(token)}>
          <MessageCircle />
          真实对话测试
        </DropdownMenuItem>
        <DropdownMenuItem disabled={busy.refreshing.has(token)} onSelect={() => actions.refresh(token)}>
          <RefreshCw />
          刷新信息和额度
        </DropdownMenuItem>
        <DropdownMenuItem disabled={busy.relogin.has(token)} onSelect={() => actions.relogin(token)}>
          <LogIn />
          重新登录
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => actions.open(account)}>
          <PanelRight />
          详情与设置
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => actions.copyToken(token)}>
          <Copy />
          复制 Token
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem danger onSelect={() => actions.remove(account)}>
          <Trash2 />
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AccountsTable({ rows, selected, busy, actions, onToggle, onToggleAll }: AccountsTableProps) {
  const visible = rows.map((row) => row.access_token);
  const headChecked = visible.length > 0 && visible.every((token) => selected.has(token));

  return (
    <table className="w-full table-fixed border-collapse text-left text-sm">
      <thead>
        <tr className="border-b border-stone-200 text-xs text-stone-400">
          <th className="w-11 py-2.5 pr-0 pl-4 font-medium">
            <Checkbox checked={headChecked} onCheckedChange={(checked) => onToggleAll(checked === true)} />
          </th>
          <th className="px-3 py-2.5 font-medium">账号</th>
          <th className="hidden w-20 px-3 py-2.5 font-medium md:table-cell">类型</th>
          <th className="w-24 px-3 py-2.5 font-medium sm:w-36">状态</th>
          <th className="hidden w-36 px-3 py-2.5 font-medium sm:table-cell">额度</th>
          <th className="hidden w-48 px-3 py-2.5 font-medium lg:table-cell">可用性</th>
          <th className="hidden w-28 px-3 py-2.5 text-right font-medium xl:table-cell">成功 / 失败</th>
          <th className="w-12 px-3 py-2.5" />
        </tr>
      </thead>
      <tbody>
        {rows.map((account) => {
          const token = account.access_token;
          const isSelected = selected.has(token);
          const type = accountType(account);
          const inflight = account.image_inflight ?? 0;
          return (
            <tr
              key={token}
              onClick={() => actions.open(account)}
              className={cn(
                "group cursor-pointer border-b border-stone-100 last:border-b-0",
                isSelected ? "bg-sky-50/70 dark:bg-sky-500/10" : "hover:bg-stone-50 dark:hover:bg-white/[0.03]",
              )}
            >
              <td className="py-2.5 pr-0 pl-4" onClick={(event) => event.stopPropagation()}>
                <Checkbox checked={isSelected} onCheckedChange={(checked) => onToggle(token, checked === true)} />
              </td>
              <td className="min-w-0 px-3 py-2.5">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate font-medium text-stone-800">{account.email || "（无邮箱）"}</span>
                  {account.has_auto_renewal ? (
                    <span
                      title="已配置自动续期：Access Token 过期后可用 Session / Refresh Token 自动换新"
                      className="shrink-0 text-emerald-600 dark:text-emerald-400"
                    >
                      <KeyRound className="size-3.5" />
                    </span>
                  ) : null}
                  {account.has_plus_promo ? (
                    <span title={account.promo_title || "有 Plus 试用资格"} className="shrink-0 text-amber-600 dark:text-amber-400">
                      <Gift className="size-3.5" />
                    </span>
                  ) : null}
                </div>
                <div className="mt-px flex items-center gap-1.5 text-xs text-stone-400">
                  <span className="font-mono">{shortToken(token)}</span>
                  <button
                    type="button"
                    title="复制 Token"
                    onClick={(event) => {
                      event.stopPropagation();
                      actions.copyToken(token);
                    }}
                    className="rounded p-0.5 opacity-0 transition group-hover:opacity-100 hover:bg-stone-100 hover:text-stone-700 focus-visible:opacity-100"
                  >
                    <Copy className="size-3.5" />
                  </button>
                </div>
              </td>
              <td className="hidden px-3 py-2.5 md:table-cell">
                <span className={cn("text-xs font-semibold tracking-wide uppercase text-stone-500", typeTone[type])}>{type}</span>
              </td>
              <td className="px-3 py-2.5">
                <div className="flex items-center gap-1.5">
                  <StatusPill status={account.status} />
                  {account.last_relogin_error ? (
                    <span
                      title={`${account.relogin_paused ? "已停止自动重登，需要手动处理。" : ""}重登失败${account.relogin_fail_count ? ` ${account.relogin_fail_count} 次` : ""}：${account.last_relogin_error}`}
                      className="hidden text-rose-500 sm:inline-flex"
                    >
                      <CircleAlert className="size-4" />
                    </span>
                  ) : null}
                </div>
              </td>
              <td className="hidden px-3 py-2.5 sm:table-cell">
                <QuotaCell account={account} />
              </td>
              <td className="hidden px-3 py-2.5 lg:table-cell">
                <div className="flex items-center gap-1.5">
                  <TestChip
                    label="快速"
                    status={account.chat_test_status}
                    checkedAt={account.chat_test_checked_at}
                    error={account.chat_test_error}
                    running={busy.chat.has(token)}
                    disabled={busy.realChat.has(token)}
                    onRun={() => actions.quickTest(token)}
                  />
                  <TestChip
                    label="真实对话"
                    status={account.real_chat_test_status}
                    checkedAt={account.real_chat_test_checked_at}
                    error={account.real_chat_test_error}
                    running={busy.realChat.has(token)}
                    disabled={busy.chat.has(token)}
                    onRun={() => actions.realTest(token)}
                  />
                </div>
              </td>
              <td className="hidden px-3 py-2.5 text-right tabular-nums xl:table-cell">
                <span className="text-stone-600">
                  {account.success} / <span className={account.fail ? "text-rose-600 dark:text-rose-400" : ""}>{account.fail}</span>
                </span>
                {inflight > 0 ? (
                  <span
                    className="block text-xs text-sky-600 dark:text-sky-400"
                    title="正在生成的图片数。号池空闲时仍持续大于 0，说明并发槽位泄漏，这个号会被调度跳过"
                  >
                    {inflight} 个生图中
                  </span>
                ) : null}
              </td>
              <td className="px-3 py-2.5 text-right">
                <RowMenu account={account} busy={busy} actions={actions} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
