"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Eye,
  EyeOff,
  FlaskConical,
  LoaderCircle,
  Mail,
  Pencil,
  Plus,
  Route,
  Save,
  Search,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
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
import {
  fetchMailboxSettings,
  resolveMailbox,
  saveMailboxSettings,
  testMailbox,
  type MailboxKind,
  type MailboxProviderSettings,
  type MailboxResolveResult,
  type MailboxSettings,
} from "@/lib/api";

const KIND_OPTIONS: { value: MailboxKind; label: string }[] = [
  { value: "cloudflare_temp_email", label: "Cloudflare Temp Email" },
  { value: "gmail_api", label: "Gmail API" },
  { value: "imap", label: "IMAP（QQ/163/Gmail 应用密码）" },
];

const KIND_LABELS: Record<string, string> = Object.fromEntries(KIND_OPTIONS.map((item) => [item.value, item.label]));

const IMAP_PRESETS = [
  { value: "qq", label: "QQ 邮箱（imap.qq.com）" },
  { value: "gmail", label: "Gmail（imap.gmail.com）" },
  { value: "163", label: "163 邮箱（imap.163.com）" },
  { value: "outlook", label: "Outlook（outlook.office365.com）" },
  { value: "custom", label: "自定义主机" },
];

type FieldDef = {
  key: string;
  label: string;
  placeholder?: string;
  secret?: boolean;
  optional?: boolean;
  help?: string;
};

// IMAP 的 preset/host/port/folders 单独渲染，这里只列普通文本字段。
const KIND_FIELDS: Record<MailboxKind, FieldDef[]> = {
  cloudflare_temp_email: [
    { key: "base_url", label: "Worker 地址", placeholder: "https://mail-api.example.com" },
    { key: "admin_password", label: "Admin 密码", secret: true },
    { key: "site_password", label: "站点密码（可选）", secret: true, optional: true },
  ],
  gmail_api: [
    { key: "client_id", label: "Client ID", placeholder: "xxxx.apps.googleusercontent.com" },
    { key: "client_secret", label: "Client Secret", secret: true },
    { key: "refresh_token", label: "Refresh Token", secret: true },
  ],
  imap: [
    { key: "username", label: "用户名", placeholder: "you@qq.com" },
    { key: "password", label: "密码", secret: true, help: "QQ 邮箱填授权码；Gmail 填应用专用密码。" },
  ],
};

const SECRET_PLACEHOLDER = "已保存，留空不修改";

const TEST_INFO_LABELS: Record<string, string> = {
  recent_messages: "近 7 天邮件",
  mailbox: "邮箱",
  total_messages: "邮件总数",
  folders: "文件夹",
};

type RouteRow = {
  key: string;
  match: string;
  provider: string;
};

let routeKeySeed = 0;
function nextRouteKey() {
  routeKeySeed += 1;
  return `route-${routeKeySeed}`;
}

function toRouteRows(routes: MailboxSettings["routes"]): RouteRow[] {
  return routes.map((route) => ({ key: nextRouteKey(), match: route.match, provider: route.provider }));
}

function textOf(value: unknown) {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function describeProvider(settings: MailboxProviderSettings) {
  if (settings.kind === "cloudflare_temp_email") {
    return textOf(settings.base_url) || "未填写地址";
  }
  if (settings.kind === "gmail_api") {
    return textOf(settings.client_id) || "未填写 Client ID";
  }
  const preset = textOf(settings.preset);
  const host = textOf(settings.host) || IMAP_PRESETS.find((item) => item.value === preset)?.label || "未填写主机";
  const username = textOf(settings.username);
  return username ? `${username} · ${host}` : host;
}

function formatTestInfo(result: Record<string, unknown>) {
  return Object.entries(result)
    .filter(([key]) => key !== "ok" && key !== "error")
    .map(([key, value]) => {
      const label = TEST_INFO_LABELS[key] || key;
      const text = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value);
      return `${label}：${text}`;
    })
    .join("，");
}

function SecretInput({
  value,
  onChange,
  placeholder,
  show,
  onToggle,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  show: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="relative">
      <Input
        type={show ? "text" : "password"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete="new-password"
        className="h-11 rounded-xl border-stone-200 bg-white pr-10"
      />
      <button
        type="button"
        className="absolute top-1/2 right-3 -translate-y-1/2 text-stone-400 transition hover:text-stone-600"
        onClick={onToggle}
      >
        {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

export function MailboxRoutingCard() {
  const didLoadRef = useRef(false);

  const [saved, setSaved] = useState<MailboxSettings | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [routeRows, setRouteRows] = useState<RouteRow[]>([]);
  const [otpTimeout, setOtpTimeout] = useState("120");
  const [pollInterval, setPollInterval] = useState("5");
  const [isSavingRoutes, setIsSavingRoutes] = useState(false);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formKind, setFormKind] = useState<MailboxKind>("cloudflare_temp_email");
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [formRequireMatch, setFormRequireMatch] = useState(true);
  const [showSecret, setShowSecret] = useState(false);
  const [isSavingMailbox, setIsSavingMailbox] = useState(false);

  const [deletingName, setDeletingName] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [testingName, setTestingName] = useState<string | null>(null);

  const [resolveEmail, setResolveEmail] = useState("");
  const [isResolving, setIsResolving] = useState(false);
  const [resolveResult, setResolveResult] = useState<MailboxResolveResult | null>(null);

  const applySettings = (data: MailboxSettings) => {
    setSaved(data);
    setRouteRows(toRouteRows(data.routes || []));
    setOtpTimeout(String(data.otp_timeout_seconds ?? 180));
    setPollInterval(String(data.poll_interval_seconds ?? 5));
  };

  useEffect(() => {
    if (didLoadRef.current) {
      return;
    }
    didLoadRef.current = true;
    void fetchMailboxSettings()
      .then(applySettings)
      .catch((error) => toast.error(error instanceof Error ? error.message : "加载邮箱路由失败"))
      .finally(() => setIsLoading(false));
  }, []);

  const providers = saved?.providers ?? {};
  const providerNames = Object.keys(providers);

  const routeCountByProvider = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const route of saved?.routes ?? []) {
      counts[route.provider] = (counts[route.provider] || 0) + 1;
    }
    return counts;
  }, [saved]);

  const routesDirty = useMemo(() => {
    if (!saved) {
      return false;
    }
    const draft = routeRows.map((row) => ({ match: row.match.trim().toLowerCase(), provider: row.provider }));
    const current = saved.routes.map((route) => ({ match: route.match, provider: route.provider }));
    return (
      JSON.stringify(draft) !== JSON.stringify(current) ||
      otpTimeout.trim() !== String(saved.otp_timeout_seconds) ||
      pollInterval.trim() !== String(saved.poll_interval_seconds)
    );
  }, [otpTimeout, pollInterval, routeRows, saved]);

  /** 把当前路由草稿和给定的邮箱集合一起提交；后端整体替换配置，空的密钥字段保留原值。 */
  const buildPayload = (nextProviders: Record<string, MailboxProviderSettings>): MailboxSettings => {
    const timeout = Number(otpTimeout);
    const interval = Number(pollInterval);
    return {
      providers: nextProviders,
      routes: routeRows
        .map((row) => ({ match: row.match.trim().toLowerCase(), provider: row.provider }))
        .filter((row) => row.match && row.provider),
      otp_timeout_seconds: Number.isFinite(timeout) && timeout > 0 ? Math.round(timeout) : saved?.otp_timeout_seconds ?? 180,
      poll_interval_seconds:
        Number.isFinite(interval) && interval > 0 ? Math.round(interval) : saved?.poll_interval_seconds ?? 5,
    };
  };

  const setFormValue = (key: string, value: string) => {
    setFormValues((prev) => ({ ...prev, [key]: value }));
  };

  const openAddDialog = () => {
    setEditingName(null);
    setFormName("");
    setFormKind("cloudflare_temp_email");
    setFormValues({ preset: "qq", port: "993", folders: "INBOX" });
    setFormRequireMatch(true);
    setShowSecret(false);
    setDialogOpen(true);
  };

  const openEditDialog = (name: string) => {
    const settings = providers[name];
    if (!settings) {
      return;
    }
    const values: Record<string, string> = {};
    for (const field of KIND_FIELDS[settings.kind] ?? []) {
      values[field.key] = field.secret ? "" : textOf(settings[field.key]);
    }
    if (settings.kind === "imap") {
      const host = textOf(settings.host);
      const preset = textOf(settings.preset);
      values.preset = host || !preset ? "custom" : preset;
      values.host = host;
      values.port = textOf(settings.port) || "993";
      values.folders = Array.isArray(settings.folders) ? settings.folders.join(", ") : "INBOX";
    } else {
      values.preset = "qq";
      values.port = "993";
      values.folders = "INBOX";
    }
    setEditingName(name);
    setFormName(name);
    setFormKind(settings.kind);
    setFormValues(values);
    setFormRequireMatch(settings.require_recipient_match !== false);
    setShowSecret(false);
    setDialogOpen(true);
  };

  const hasStoredSecret = (key: string) => {
    if (!editingName) {
      return false;
    }
    const settings = providers[editingName];
    return Boolean(settings && settings.kind === formKind && settings[`has_${key}`]);
  };

  const handleSaveMailbox = async () => {
    const name = formName.trim();
    if (!name) {
      toast.error("请输入邮箱名称");
      return;
    }
    if (!editingName && providers[name]) {
      toast.error("已存在同名邮箱");
      return;
    }
    for (const field of KIND_FIELDS[formKind]) {
      const value = (formValues[field.key] || "").trim();
      if (!value && !field.optional && !(field.secret && hasStoredSecret(field.key))) {
        toast.error(`请填写${field.label}`);
        return;
      }
    }
    const isCustomHost = formKind === "imap" && formValues.preset === "custom";
    if (isCustomHost && !(formValues.host || "").trim()) {
      toast.error("请填写 IMAP 主机");
      return;
    }

    const previous = editingName ? providers[editingName] : undefined;
    const base: Record<string, unknown> = {};
    if (previous && previous.kind === formKind) {
      for (const [key, value] of Object.entries(previous)) {
        if (!key.startsWith("has_")) {
          base[key] = value;
        }
      }
    }
    const next: MailboxProviderSettings = {
      ...base,
      kind: formKind,
      require_recipient_match: formRequireMatch,
    };
    for (const field of KIND_FIELDS[formKind]) {
      next[field.key] = (formValues[field.key] || "").trim();
    }
    if (formKind === "imap") {
      const port = Number(formValues.port);
      const folders = (formValues.folders || "")
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter(Boolean);
      next.preset = formValues.preset || "custom";
      next.host = isCustomHost ? (formValues.host || "").trim() : "";
      next.port = Number.isFinite(port) && port > 0 ? Math.round(port) : 993;
      next.folders = folders.length ? folders : ["INBOX"];
    }

    setIsSavingMailbox(true);
    try {
      const data = await saveMailboxSettings(buildPayload({ ...providers, [name]: next }));
      applySettings(data);
      setDialogOpen(false);
      toast.success(editingName ? "邮箱已更新" : "邮箱已添加");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存邮箱失败");
    } finally {
      setIsSavingMailbox(false);
    }
  };

  const handleDeleteMailbox = async () => {
    if (!deletingName) {
      return;
    }
    const nextProviders = { ...providers };
    delete nextProviders[deletingName];
    const removedRoutes = routeRows.filter((row) => row.provider === deletingName).length;
    const payload = buildPayload(nextProviders);
    payload.routes = payload.routes.filter((route) => route.provider !== deletingName);

    setIsDeleting(true);
    try {
      applySettings(await saveMailboxSettings(payload));
      toast.success(removedRoutes ? `邮箱已删除，同时移除 ${removedRoutes} 条路由` : "邮箱已删除");
      setDeletingName(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除邮箱失败");
    } finally {
      setIsDeleting(false);
    }
  };

  const handleTestMailbox = async (name: string) => {
    setTestingName(name);
    try {
      const result = await testMailbox(name);
      if (result.ok) {
        const info = formatTestInfo(result);
        toast.success(info ? `「${name}」连接正常：${info}` : `「${name}」连接正常`);
      } else {
        toast.error(`「${name}」测试失败：${result.error || "未知错误"}`);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "测试邮箱失败");
    } finally {
      setTestingName(null);
    }
  };

  const handleSaveRoutes = async () => {
    const timeout = Number(otpTimeout);
    const interval = Number(pollInterval);
    if (!Number.isFinite(timeout) || timeout < 10) {
      toast.error("验证码等待秒数不能小于 10");
      return;
    }
    if (!Number.isFinite(interval) || interval < 1) {
      toast.error("轮询间隔秒数不能小于 1");
      return;
    }
    const incomplete = routeRows.find((row) => !row.match.trim() || !row.provider);
    if (incomplete) {
      toast.error("每条路由都需要填写匹配规则并选择邮箱");
      return;
    }
    setIsSavingRoutes(true);
    try {
      applySettings(await saveMailboxSettings(buildPayload(providers)));
      toast.success("邮箱路由已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存路由失败");
    } finally {
      setIsSavingRoutes(false);
    }
  };

  const handleResolve = async () => {
    const email = resolveEmail.trim();
    if (!email.includes("@")) {
      toast.error("请输入完整邮箱地址");
      return;
    }
    setIsResolving(true);
    try {
      setResolveResult(await resolveMailbox(email));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "查询路由失败");
    } finally {
      setIsResolving(false);
    }
  };

  const updateRouteRow = (key: string, patch: Partial<RouteRow>) => {
    setRouteRows((prev) => prev.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  };

  const isCustomHost = formValues.preset === "custom";

  return (
    <>
      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-6 p-6">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-xl bg-stone-100">
                <Mail className="size-5 text-stone-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold tracking-tight">邮箱路由</h2>
                <p className="text-sm text-stone-500">
                  账号自动重新登录时，如果 OpenAI 要求邮箱验证码，会按账号邮箱的域名找到对应邮箱读取验证码。
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {providerNames.length > 0 ? (
                <Badge className="rounded-md px-2.5 py-1">{providerNames.length} 个邮箱</Badge>
              ) : null}
              <Button
                className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
                onClick={openAddDialog}
                disabled={isLoading}
              >
                <Plus className="size-4" />
                添加邮箱
              </Button>
            </div>
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center py-10">
              <LoaderCircle className="size-5 animate-spin text-stone-400" />
            </div>
          ) : (
            <>
              {saved?.env_pending ? (
                <div className="rounded-xl bg-sky-50 px-4 py-3 text-xs leading-5 text-sky-800">
                  下面的邮箱目前读自服务器的 mailbox.env。在这里保存一次之后，就以网页上的设置为准，可以直接编辑、删除和新增，之后不用再改 mailbox.env。
                </div>
              ) : null}
              {providerNames.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-3 rounded-xl bg-stone-50 px-6 py-10 text-center">
                  <Mail className="size-8 text-stone-300" />
                  <div className="space-y-1">
                    <p className="text-sm font-medium text-stone-600">暂无邮箱</p>
                    <p className="text-sm text-stone-400">点击「添加邮箱」配置接收验证码的邮箱。</p>
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  {providerNames.map((name) => {
                    const settings = providers[name];
                    const routeCount = routeCountByProvider[name] || 0;
                    return (
                      <div
                        key={name}
                        className="flex flex-col gap-3 rounded-xl border border-stone-200 bg-white px-4 py-3"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-stone-800">{name}</span>
                              <Badge variant="secondary" className="rounded-md bg-stone-100 text-stone-600">
                                {KIND_LABELS[settings.kind] || settings.kind}
                              </Badge>
                              {routeCount > 0 ? (
                                <Badge variant="info" className="rounded-md">
                                  {routeCount} 条路由
                                </Badge>
                              ) : (
                                <Badge variant="warning" className="rounded-md">
                                  未被路由使用
                                </Badge>
                              )}
                            </div>
                            <div className="truncate text-xs text-stone-400">
                              {describeProvider(settings)}
                              {settings.require_recipient_match === false ? " · 不按收件人过滤" : " · 按收件人过滤"}
                            </div>
                          </div>
                          <div className="flex items-center gap-1">
                            <Button
                              variant="outline"
                              className="h-8 rounded-lg border-stone-200 bg-white px-3 text-xs text-stone-600"
                              onClick={() => void handleTestMailbox(name)}
                              disabled={testingName !== null}
                            >
                              {testingName === name ? (
                                <LoaderCircle className="size-3.5 animate-spin" />
                              ) : (
                                <FlaskConical className="size-3.5" />
                              )}
                              测试
                            </Button>
                            <button
                              type="button"
                              className="rounded-lg p-2 text-stone-400 transition hover:bg-stone-100 hover:text-stone-700"
                              onClick={() => openEditDialog(name)}
                              title="编辑"
                            >
                              <Pencil className="size-4" />
                            </button>
                            <button
                              type="button"
                              className="rounded-lg p-2 text-stone-400 transition hover:bg-rose-50 hover:text-rose-500"
                              onClick={() => setDeletingName(name)}
                              title="删除"
                            >
                              <Trash2 className="size-4" />
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              <div className="space-y-3 rounded-xl border border-stone-200 bg-white px-4 py-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-sm font-medium text-stone-700">
                    <Route className="size-4 text-stone-500" />
                    路由规则
                  </div>
                  <Button
                    variant="outline"
                    className="h-8 rounded-lg border-stone-200 bg-white px-3 text-xs text-stone-600"
                    onClick={() =>
                      setRouteRows((prev) => [
                        ...prev,
                        { key: nextRouteKey(), match: "", provider: providerNames[0] ?? "" },
                      ])
                    }
                    disabled={providerNames.length === 0}
                  >
                    <Plus className="size-3.5" />
                    添加路由
                  </Button>
                </div>
                {routeRows.length === 0 ? (
                  <div className="rounded-lg bg-stone-50 px-3 py-6 text-center text-sm text-stone-400">
                    {providerNames.length === 0 ? "先添加邮箱，再配置路由。" : "暂无路由，点击「添加路由」。"}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_36px] gap-2 px-1 text-xs text-stone-400">
                      <span>匹配（域名 / 完整邮箱 / *）</span>
                      <span>使用邮箱</span>
                      <span />
                    </div>
                    {routeRows.map((row) => (
                      <div key={row.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_36px] items-center gap-2">
                        <Input
                          value={row.match}
                          onChange={(event) => updateRouteRow(row.key, { match: event.target.value })}
                          placeholder="例如：040601.com"
                          className="h-10 rounded-xl border-stone-200 bg-white"
                        />
                        <Select value={row.provider} onValueChange={(value) => updateRouteRow(row.key, { provider: value })}>
                          <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white">
                            <SelectValue placeholder="选择邮箱" />
                          </SelectTrigger>
                          <SelectContent>
                            {providerNames.map((name) => (
                              <SelectItem key={name} value={name}>
                                {name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <button
                          type="button"
                          className="flex size-9 items-center justify-center rounded-lg text-stone-400 transition hover:bg-rose-50 hover:text-rose-500"
                          onClick={() => setRouteRows((prev) => prev.filter((item) => item.key !== row.key))}
                          title="删除路由"
                        >
                          <Trash2 className="size-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                <div className="grid gap-3 pt-1 sm:grid-cols-2">
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-stone-700">验证码等待秒数（超时结束本次导入）</label>
                    <Input
                      type="number"
                      min={10}
                      value={otpTimeout}
                      onChange={(event) => setOtpTimeout(event.target.value)}
                      className="h-10 rounded-xl border-stone-200 bg-white"
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-stone-700">轮询间隔秒数</label>
                    <Input
                      type="number"
                      min={1}
                      value={pollInterval}
                      onChange={(event) => setPollInterval(event.target.value)}
                      className="h-10 rounded-xl border-stone-200 bg-white"
                    />
                  </div>
                </div>

                <div className="flex items-center justify-end gap-2">
                  {routesDirty ? <span className="text-xs text-amber-600">有未保存的修改</span> : null}
                  <Button
                    variant="secondary"
                    className="h-9 rounded-xl bg-stone-100 px-4 text-stone-700 hover:bg-stone-200"
                    onClick={() => saved && applySettings(saved)}
                    disabled={!routesDirty || isSavingRoutes}
                  >
                    撤销
                  </Button>
                  <Button
                    className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
                    onClick={() => void handleSaveRoutes()}
                    disabled={!routesDirty || isSavingRoutes}
                  >
                    {isSavingRoutes ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
                    保存路由
                  </Button>
                </div>
              </div>

              <div className="space-y-2 rounded-xl border border-stone-200 bg-white px-4 py-4">
                <div className="flex items-center gap-2 text-sm font-medium text-stone-700">
                  <Search className="size-4 text-stone-500" />
                  查路由
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Input
                    value={resolveEmail}
                    onChange={(event) => setResolveEmail(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        void handleResolve();
                      }
                    }}
                    placeholder="输入账号邮箱，例如 someone@040601.com"
                    className="h-10 rounded-xl border-stone-200 bg-white"
                  />
                  <Button
                    variant="outline"
                    className="h-10 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                    onClick={() => void handleResolve()}
                    disabled={isResolving}
                  >
                    {isResolving ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}
                    查路由
                  </Button>
                </div>
                {resolveResult ? (
                  <div className="text-sm text-stone-600">
                    {resolveResult.error ? (
                      <span className="text-rose-600">查询出错：{resolveResult.error}</span>
                    ) : resolveResult.mailbox ? (
                      <span>
                        {resolveResult.email} → <span className="font-medium text-stone-800">{resolveResult.mailbox}</span>
                        {resolveResult.kind ? `（${KIND_LABELS[resolveResult.kind] || resolveResult.kind}）` : ""}
                      </span>
                    ) : (
                      <span className="text-amber-600">{resolveResult.email} 没有匹配的路由，登录需要验证码时会失败。</span>
                    )}
                    {routesDirty ? <span className="ml-2 text-xs text-stone-400">（按已保存的路由查询）</span> : null}
                  </div>
                ) : null}
              </div>
            </>
          )}

          <div className="rounded-xl bg-stone-50 px-4 py-3 text-sm leading-6 text-stone-500">
            <p className="font-medium text-stone-600">使用说明</p>
            <ul className="mt-1 list-inside list-disc space-y-0.5">
              <li>先添加邮箱，再用路由把账号邮箱的域名指向它；* 表示兜底。</li>
              <li>匹配优先级：完整邮箱地址 &gt; 更长的域名 &gt; *；子域名也会命中上级域名的规则。</li>
              <li>邮箱的增删改会立即保存；路由和秒数修改后需点「保存路由」。</li>
              <li>密钥不会回显，编辑时留空表示沿用已保存的值。</li>
            </ul>
          </div>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={(open) => (!isSavingMailbox ? setDialogOpen(open) : null)}>
        <DialogContent showCloseButton={false} className="max-h-[90vh] overflow-y-auto rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>{editingName ? "编辑邮箱" : "添加邮箱"}</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              {editingName ? `修改邮箱「${editingName}」的连接信息` : "添加一个用来读取 OpenAI 登录验证码的邮箱"}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">名称</label>
              <Input
                value={formName}
                onChange={(event) => setFormName(event.target.value)}
                placeholder="例如：cf-temp、qq-main"
                disabled={Boolean(editingName)}
                className="h-11 rounded-xl border-stone-200 bg-white"
              />
              {editingName ? <div className="text-xs text-stone-400">名称用于路由引用，保存后不可修改。</div> : null}
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">类型</label>
              <Select value={formKind} onValueChange={(value) => setFormKind(value as MailboxKind)}>
                <SelectTrigger className="h-11 rounded-xl border-stone-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {KIND_OPTIONS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {formKind === "imap" ? (
              <>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-stone-700">服务商</label>
                  <Select value={formValues.preset || "qq"} onValueChange={(value) => setFormValue("preset", value)}>
                    <SelectTrigger className="h-11 rounded-xl border-stone-200 bg-white">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {IMAP_PRESETS.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className={isCustomHost ? "grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]" : "space-y-2"}>
                  {isCustomHost ? (
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-stone-700">IMAP 主机</label>
                      <Input
                        value={formValues.host || ""}
                        onChange={(event) => setFormValue("host", event.target.value)}
                        placeholder="imap.example.com"
                        className="h-11 rounded-xl border-stone-200 bg-white"
                      />
                    </div>
                  ) : null}
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-stone-700">端口</label>
                    <Input
                      type="number"
                      value={formValues.port || ""}
                      onChange={(event) => setFormValue("port", event.target.value)}
                      placeholder="993"
                      className="h-11 rounded-xl border-stone-200 bg-white"
                    />
                  </div>
                </div>
              </>
            ) : null}

            {KIND_FIELDS[formKind].map((field) => {
              const stored = field.secret && hasStoredSecret(field.key);
              const placeholder = stored ? SECRET_PLACEHOLDER : field.placeholder || field.label;
              return (
                <div key={`${formKind}-${field.key}`} className="space-y-2">
                  <label className="text-sm font-medium text-stone-700">{field.label}</label>
                  {field.secret ? (
                    <SecretInput
                      value={formValues[field.key] || ""}
                      onChange={(value) => setFormValue(field.key, value)}
                      placeholder={placeholder}
                      show={showSecret}
                      onToggle={() => setShowSecret((prev) => !prev)}
                    />
                  ) : (
                    <Input
                      value={formValues[field.key] || ""}
                      onChange={(event) => setFormValue(field.key, event.target.value)}
                      placeholder={placeholder}
                      className="h-11 rounded-xl border-stone-200 bg-white"
                    />
                  )}
                  {field.help ? <div className="text-xs text-stone-400">{field.help}</div> : null}
                </div>
              );
            })}

            {formKind === "imap" ? (
              <div className="space-y-2">
                <label className="text-sm font-medium text-stone-700">文件夹</label>
                <Input
                  value={formValues.folders || ""}
                  onChange={(event) => setFormValue("folders", event.target.value)}
                  placeholder="INBOX"
                  className="h-11 rounded-xl border-stone-200 bg-white"
                />
                <div className="text-xs text-stone-400">多个用逗号分隔，留空默认 INBOX；验证码可能进垃圾箱时把垃圾箱也加上。</div>
              </div>
            ) : null}

            <label className="flex cursor-pointer items-start gap-3 rounded-xl bg-stone-50 px-3 py-3">
              <Checkbox
                checked={formRequireMatch}
                onCheckedChange={(checked) => setFormRequireMatch(Boolean(checked))}
                className="mt-0.5"
              />
              <div className="space-y-0.5">
                <div className="text-sm font-medium text-stone-700">按收件人过滤</div>
                <div className="text-xs leading-5 text-stone-500">
                  共享收件箱（如域名转发到同一个 Gmail）时按收件人过滤。
                </div>
              </div>
            </label>
          </div>
          <DialogFooter className="pt-2">
            <Button
              variant="secondary"
              className="h-10 rounded-xl bg-stone-100 px-5 text-stone-700 hover:bg-stone-200"
              onClick={() => setDialogOpen(false)}
              disabled={isSavingMailbox}
            >
              取消
            </Button>
            <Button
              className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800"
              onClick={() => void handleSaveMailbox()}
              disabled={isSavingMailbox}
            >
              {isSavingMailbox ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
              {editingName ? "保存修改" : "添加"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deletingName !== null} onOpenChange={(open) => (!open && !isDeleting ? setDeletingName(null) : null)}>
        <DialogContent showCloseButton={false} className="rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>删除邮箱</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              确认删除邮箱「{deletingName}」吗？保存的密钥会一起删除，指向它的
              {deletingName ? ` ${routeRows.filter((row) => row.provider === deletingName).length} ` : " "}
              条路由也会被移除。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" className="rounded-xl" onClick={() => setDeletingName(null)} disabled={isDeleting}>
              取消
            </Button>
            <Button
              className="rounded-xl bg-rose-600 text-white hover:bg-rose-700"
              onClick={() => void handleDeleteMailbox()}
              disabled={isDeleting}
            >
              {isDeleting ? <LoaderCircle className="size-4 animate-spin" /> : null}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
