"use client";

import { useState, type ReactNode } from "react";
import { Copy, ExternalLink, Gift, KeyRound, Link2, LoaderCircle, LogIn, Play, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { createTeamTrialCheckout, type Account, type AccountStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

import { accountType, ago, copyText, featureLabel, fullTime, shortToken, STATUS_ORDER, typeTone, until, untilPlain } from "../lib/format";
import { StatusPill, TestChip, type RowActions, type RowBusy } from "./accounts-table";

type AccountDrawerProps = {
  account: Account | null;
  open: boolean;
  onClose: () => void;
  busy: RowBusy;
  actions: RowActions;
  saving: boolean;
  onSave: (token: string, patch: { status: AccountStatus; proxy: string }) => void;
  testingProxy: boolean;
  onTestProxy: (proxy: string) => void;
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-stone-100 py-4 last:border-b-0">
      <h3 className="mb-2.5 text-xs font-semibold tracking-wider text-stone-400">{title}</h3>
      {children}
    </section>
  );
}

function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[104px_1fr] gap-x-3 gap-y-1.5 text-[13px]">
      {rows.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-stone-500">{key}</dt>
          <dd className="flex min-w-0 items-center gap-1.5 break-all text-stone-800">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ErrorBox({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2 rounded-lg bg-rose-50 px-2.5 py-2 text-xs break-all text-rose-700 dark:bg-rose-500/15 dark:text-rose-300">
      {children}
    </div>
  );
}

function TestRow(props: {
  title: string;
  desc: string;
  status?: Account["chat_test_status"];
  checkedAt?: string | null;
  error?: string | null;
  running: boolean;
  disabled: boolean;
  onRun: () => void;
  extra?: ReactNode;
}) {
  const tested = props.status && props.status !== "未测试";
  return (
    <>
      <div className="flex items-center gap-2.5 py-1.5">
        <TestChip label={props.title} status={props.status} checkedAt={props.checkedAt} running={props.running} />
        <div className="min-w-0 flex-1">
          <div className="text-[13px] text-stone-800">{props.title}</div>
          <div className="text-xs text-stone-400">
            {props.desc}
            {tested && props.checkedAt ? ` · ${ago(props.checkedAt)}` : ""}
          </div>
        </div>
        {props.extra}
        <Button
          variant="outline"
          size="sm"
          className="h-7 rounded-lg border-stone-200 px-2.5"
          disabled={props.disabled}
          onClick={props.onRun}
        >
          {props.running ? <LoaderCircle className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
          运行
        </Button>
      </div>
      {props.error && (props.status === "不可用" || props.status === "测试失败") ? <ErrorBox>{props.error}</ErrorBox> : null}
    </>
  );
}

function QuotaSection({ account }: { account: Account }) {
  if (account.status !== "正常" && account.status !== "限流") {
    return <div className="text-[13px] text-stone-400">账号{account.status}，额度不可用。</div>;
  }
  const limits = (account.limits_progress ?? []).filter((item) => item.feature_name);
  return (
    <>
      <div className="flex items-baseline justify-between text-[13px]">
        <span className="text-stone-600">
          图片额度剩余{" "}
          <b className="text-base font-semibold text-stone-900 tabular-nums">
            {account.status === "限流" ? 0 : Math.max(0, account.quota)}
          </b>
        </span>
        <span className="text-xs text-stone-400" title={fullTime(account.restore_at)}>
          {until(account.restore_at)}
        </span>
      </div>
      {limits.length > 0 ? (
        <div className="mt-3">
          <KV
            rows={limits.map((item) => [
              featureLabel(item.feature_name),
              <span key="v" title={item.reset_after ? `重置时间 ${fullTime(item.reset_after)}` : undefined}>
                <span className="tabular-nums">{item.remaining ?? "—"}</span>
                {item.reset_after ? <span className="ml-1.5 text-xs text-stone-400">{untilPlain(item.reset_after)}重置</span> : null}
              </span>,
            ])}
          />
        </div>
      ) : null}
    </>
  );
}

export function AccountDrawer(props: AccountDrawerProps) {
  const { account, open, onClose } = props;
  return (
    <Sheet open={open && Boolean(account)} onOpenChange={(next) => (!next ? onClose() : null)}>
      <SheetContent side="right" showCloseButton={false} className="w-full gap-0 border-stone-200 bg-popover p-0 sm:max-w-[460px]">
        {/* Content unmounts on close and remounts per account, which resets the form. */}
        {account ? <DrawerBody key={account.access_token} {...props} account={account} /> : null}
      </SheetContent>
    </Sheet>
  );
}

function DrawerBody({
  account,
  onClose,
  busy,
  actions,
  saving,
  onSave,
  testingProxy,
  onTestProxy,
}: AccountDrawerProps & { account: Account }) {
  const [status, setStatus] = useState<AccountStatus>(account.status);
  const [proxy, setProxy] = useState(account.proxy ?? "");
  const [workspaceName, setWorkspaceName] = useState("MyTeam");
  const [seatQuantity, setSeatQuantity] = useState("5");
  const [currency, setCurrency] = useState("USD");
  const [creatingCheckout, setCreatingCheckout] = useState(false);
  const [checkoutUrl, setCheckoutUrl] = useState("");
  const [checkoutError, setCheckoutError] = useState("");
  const token = account.access_token;
  const dirty = status !== account.status || proxy.trim() !== (account.proxy ?? "").trim();
  const testing = busy.chat.has(token) || busy.realChat.has(token);
  const type = accountType(account);

  const handleGenerateTeamCheckout = async () => {
    const seats = Math.max(1, Math.min(100, Number.parseInt(seatQuantity, 10) || 5));
    setCreatingCheckout(true);
    setCheckoutError("");
    try {
      const result = await createTeamTrialCheckout({
        access_token: token,
        workspace_name: workspaceName.trim() || "MyTeam",
        seat_quantity: seats,
        currency,
      });
      setCheckoutUrl(result.url);
      try {
        await copyText(result.url);
        toast.success(`Team 试用结账链接已生成并复制（${result.country}/${result.currency}）`);
      } catch {
        toast.success(`Team 试用结账链接已生成（${result.country}/${result.currency}）`);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "生成 Team 结账链接失败";
      setCheckoutError(message);
      toast.error(message);
    } finally {
      setCreatingCheckout(false);
    }
  };

  return (
    <>
      <div className="flex items-start gap-3 border-b border-stone-100 px-5 pt-5 pb-3.5">
        <div className="min-w-0 flex-1">
          <SheetTitle className="text-base font-semibold break-all text-stone-900">{account.email || "（无邮箱）"}</SheetTitle>
          <SheetDescription className="sr-only">账号详情与设置</SheetDescription>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <StatusPill status={account.status} />
            <span className={cn("text-xs font-semibold tracking-wide uppercase text-stone-500", typeTone[type])}>{type}</span>
            {account.has_plus_promo ? (
              <span className="inline-flex h-[22px] items-center gap-1 rounded-full bg-amber-50 px-2 text-xs font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
                <Gift className="size-3.5" />
                {account.promo_title || "Plus 试用"}
              </span>
            ) : null}
            {account.has_team_promo ? (
              <span className="inline-flex h-[22px] items-center gap-1 rounded-full bg-sky-50 px-2 text-xs font-medium text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
                <Gift className="size-3.5" />
                {account.team_promo_title || "Team 试用"}
              </span>
            ) : null}
          </div>
        </div>
        <Button variant="ghost" size="icon" className="size-8 rounded-lg" onClick={onClose} title="关闭">
          <X className="size-4" />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 pb-5">
        {account.has_team_promo || account.has_plus_promo ? (
          <Section title="试用优惠">
            {account.has_team_promo ? (
              <div className="space-y-2.5">
                <div className="flex items-center justify-between gap-2 text-[13px]">
                  <span className="font-medium text-sky-700 dark:text-sky-300">
                    {account.team_promo_title || "Try Business free for 1 month"}
                  </span>
                  <span className="font-mono text-xs text-stone-400">{account.team_promo_campaign_id || "team-1-month-free"}</span>
                </div>
                <div className="grid grid-cols-[1fr_72px_84px] gap-2">
                  <Input
                    value={workspaceName}
                    onChange={(event) => setWorkspaceName(event.target.value)}
                    placeholder="工作区名称"
                    className="h-8 rounded-lg border-stone-200 bg-white text-xs"
                  />
                  <Input
                    type="number"
                    min={1}
                    max={100}
                    value={seatQuantity}
                    onChange={(event) => setSeatQuantity(event.target.value)}
                    title="席位数"
                    className="h-8 rounded-lg border-stone-200 bg-white text-xs tabular-nums"
                  />
                  <Select value={currency} onValueChange={setCurrency}>
                    <SelectTrigger className="h-8 rounded-lg border-stone-200 bg-white text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="USD">USD</SelectItem>
                      <SelectItem value="SGD">SGD</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 rounded-lg border-sky-200 bg-sky-50/70 px-3 text-sky-700 hover:bg-sky-100/70 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300"
                    disabled={creatingCheckout}
                    onClick={() => void handleGenerateTeamCheckout()}
                  >
                    {creatingCheckout ? <LoaderCircle className="size-3.5 animate-spin" /> : <Gift className="size-3.5" />}
                    生成 Team 试用结账链接
                  </Button>
                </div>
                {checkoutError ? <ErrorBox>{checkoutError}</ErrorBox> : null}
                {checkoutUrl ? (
                  <div className="rounded-lg border border-stone-200 bg-stone-50 p-2.5 text-xs">
                    <div className="mb-2 font-mono break-all text-stone-700 line-clamp-2">{checkoutUrl}</div>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 rounded-lg border-stone-200 bg-white px-2.5 text-xs"
                        onClick={() =>
                          void copyText(checkoutUrl).then(
                            () => toast.success("已复制结账链接"),
                            () => toast.error("复制失败"),
                          )
                        }
                      >
                        <Copy className="size-3" />
                        复制链接
                      </Button>
                      <a
                        href={checkoutUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex h-7 items-center gap-1 rounded-lg border border-stone-200 bg-white px-2.5 text-xs font-medium text-stone-700 hover:bg-stone-100"
                      >
                        <ExternalLink className="size-3" />
                        打开结账页
                      </a>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
            {account.has_plus_promo ? (
              <div className={cn("flex items-center justify-between gap-2 text-[13px]", account.has_team_promo && "mt-3 border-t border-stone-100 pt-3")}>
                <span className="text-amber-700 dark:text-amber-300">{account.promo_title || "Try Plus free for 1 month"}</span>
                <a
                  href="https://chatgpt.com/?promo_campaign=plus-1-month-free#pricing"
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg border border-stone-200 bg-white px-2.5 text-xs font-medium text-stone-700 hover:bg-stone-50"
                >
                  <ExternalLink className="size-3" />
                  官网优惠入口
                </a>
              </div>
            ) : null}
          </Section>
        ) : null}

        <Section title="额度">
          <QuotaSection account={account} />
        </Section>

        <Section title="可用性测试">
          <TestRow
            title="快速测试"
            desc="发一条固定短消息，测试对话会被隐藏"
            status={account.chat_test_status}
            checkedAt={account.chat_test_checked_at}
            error={account.chat_test_error}
            running={busy.chat.has(token)}
            disabled={testing}
            onRun={() => actions.quickTest(token)}
          />
          <TestRow
            title="真实对话测试"
            desc="发一条随机短问题，会留在 ChatGPT 历史里"
            status={account.real_chat_test_status}
            checkedAt={account.real_chat_test_checked_at}
            error={account.real_chat_test_error}
            running={busy.realChat.has(token)}
            disabled={testing}
            onRun={() => actions.realTest(token)}
            extra={
              account.real_chat_test_conversation_url ? (
                <a
                  href={account.real_chat_test_conversation_url}
                  target="_blank"
                  rel="noreferrer"
                  title="打开最近一次测试对话"
                  className="inline-flex size-7 items-center justify-center rounded-lg text-stone-400 hover:bg-stone-100 hover:text-stone-700"
                >
                  <ExternalLink className="size-3.5" />
                </a>
              ) : null
            }
          />
        </Section>

        <Section title="登录与续期">
          <KV
            rows={[
              [
                "自动续期",
                account.has_auto_renewal ? (
                  <>
                    <KeyRound className="size-3.5 text-emerald-600 dark:text-emerald-400" />
                    已配置
                  </>
                ) : (
                  <span className="text-stone-400">未配置（只有 Access Token）</span>
                ),
              ],
              [
                "Token 到期",
                <span key="v" title={fullTime(account.access_token_expires_at)}>
                  {untilPlain(account.access_token_expires_at)}
                </span>,
              ],
              [
                "登录会话到期",
                <span key="v" title={fullTime(account.auth_session_expires_at)}>
                  {untilPlain(account.auth_session_expires_at)}
                </span>,
              ],
              [
                "最近重登",
                <span key="v" title={fullTime(account.last_relogin_at)}>
                  {ago(account.last_relogin_at)}
                </span>,
              ],
            ]}
          />
          {account.last_relogin_error ? (
            <ErrorBox>
              最近一次重登失败{account.relogin_fail_count ? `（共 ${account.relogin_fail_count} 次）` : ""}：{account.last_relogin_error}
              {account.relogin_paused ? <div className="mt-1 font-medium">已停止自动重登，需要你点「立即重新登录」手动处理。</div> : null}
            </ErrorBox>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            className="mt-3 h-7 rounded-lg border-stone-200 px-2.5"
            disabled={busy.relogin.has(token)}
            onClick={() => actions.relogin(token)}
          >
            {busy.relogin.has(token) ? <LoaderCircle className="size-3.5 animate-spin" /> : <LogIn className="size-3.5" />}
            立即重新登录
          </Button>
        </Section>

        <Section title="账号信息">
          <KV
            rows={[
              [
                "Token",
                <>
                  <span className="font-mono text-xs">{shortToken(token)}</span>
                  <button
                    type="button"
                    title="复制 Token"
                    onClick={() => actions.copyToken(token)}
                    className="rounded p-0.5 text-stone-400 hover:bg-stone-100 hover:text-stone-700"
                  >
                    <Copy className="size-3.5" />
                  </button>
                </>,
              ],
              ["来源", account.source_type || "web"],
              ["添加时间", fullTime(account.created_at)],
              ["最近使用", ago(account.last_used_at)],
              ["成功 / 失败", `${account.success} / ${account.fail}`],
              ["生图中", `${account.image_inflight ?? 0} 个`],
            ]}
          />
        </Section>

        <Section title="设置">
          <label className="mb-1.5 block text-[13px] text-stone-500">状态</label>
          <Select value={status} onValueChange={(value) => setStatus(value as AccountStatus)}>
            <SelectTrigger className="h-9 w-full rounded-lg border-stone-200 bg-white">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_ORDER.map((value) => (
                <SelectItem key={value} value={value}>
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <label className="mt-3 mb-1.5 block text-[13px] text-stone-500">账号代理</label>
          <div className="flex gap-2">
            <Input
              value={proxy}
              onChange={(event) => setProxy(event.target.value)}
              placeholder="留空走全局代理，例如 http://127.0.0.1:7890"
              className="h-9 rounded-lg border-stone-200 bg-white"
            />
            <Button
              variant="outline"
              className="h-9 rounded-lg border-stone-200 px-3"
              disabled={testingProxy || !proxy.trim()}
              onClick={() => onTestProxy(proxy.trim())}
            >
              {testingProxy ? <LoaderCircle className="size-4 animate-spin" /> : <Link2 className="size-4" />}
              测试
            </Button>
          </div>
        </Section>
      </div>

      <div className="flex items-center gap-2 border-t border-stone-100 px-5 py-3">
        <Button
          className="h-9 rounded-lg bg-stone-950 px-4 text-white hover:bg-stone-800"
          disabled={!dirty || saving}
          onClick={() => onSave(token, { status, proxy: proxy.trim() })}
        >
          {saving ? <LoaderCircle className="size-4 animate-spin" /> : null}
          保存
        </Button>
        <Button variant="ghost" className="h-9 rounded-lg px-3" onClick={onClose}>
          {dirty ? "放弃修改" : "关闭"}
        </Button>
        <div className="flex-1" />
        <Button
          variant="ghost"
          className="h-9 rounded-lg px-3 text-rose-600 hover:bg-rose-50 hover:text-rose-700 dark:text-rose-400"
          onClick={() => actions.remove(account)}
        >
          <Trash2 className="size-4" />
          删除账号
        </Button>
      </div>
    </>
  );
}
