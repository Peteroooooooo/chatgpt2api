"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  LoaderCircle,
  RefreshCw,
  Save,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  fetchModelCatalog,
  refreshModelCatalog,
  saveModelRules,
  type CatalogModel,
  type FeatureModels,
  type ModelCatalog,
  type ModelRuleChange,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const REASONING_LABELS: Record<string, string> = {
  auto: "自动",
  none: "快速",
  reasoning: "思考",
};

const POLICY_TEXT: Record<ModelCatalog["default_policy"], string> = {
  free_exposed: "Free 也有的默认显示；只有 Plus / Team / Pro 有的默认隐藏",
  all_exposed: "全部显示（含内部测试模型）",
  all_hidden: "默认隐藏，打开才对外",
};

const FEATURES: { key: keyof FeatureModels; label: string; help: string }[] = [
  {
    key: "search_model",
    label: "联网搜索",
    help: "/v1/search 和带 web_search 工具的对话请求：请求里写的模型号池有公开就用它，否则用这里选的模型（默认 auto）。只派给允许接它的账号。",
  },
  {
    key: "real_chat_model",
    label: "真实对话测试",
    help: "号池里手动点「真实对话测试」时发送用的模型。",
  },
  {
    key: "daily_chat_model",
    label: "每日对话",
    help: "每日对话给每个账号发送时用的模型。",
  },
];

type Rule = { exposed: boolean; account_types: string[] };

function timeText(value?: string | null) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function shortId(id: string) {
  const match = /^chatgpt_alpha_model_external_access_reserved_(.+)$/.exec(id);
  return match ? `…reserved_${match[1]}` : id;
}

/** Groups like "GPT-6", "GPT-6.1", "GPT-5.6"; anything else goes to 其它. */
function familyOf(id: string): { key: string; label: string; order: number } {
  const match = /^gpt-(\d+)(?:[.-](\d+)(?=-|$))?/.exec(id);
  if (!match) {
    return { key: "other", label: "其它", order: -1 };
  }
  const major = Number(match[1]);
  const minor = match[2] ? Number(match[2]) : 0;
  const version = minor ? `${major}.${minor}` : `${major}`;
  return {
    key: version,
    label: `GPT-${version} 系列`,
    order: major * 1000 + minor,
  };
}

function groupModels(models: CatalogModel[]) {
  const groups = new Map<
    string,
    { label: string; order: number; items: CatalogModel[] }
  >();
  for (const model of models) {
    const family =
      model.id === "auto"
        ? { key: "auto", label: "自动", order: 1_000_000 }
        : familyOf(model.id);
    const group = groups.get(family.key) ?? {
      label: family.label,
      order: family.order,
      items: [],
    };
    group.items.push(model);
    groups.set(family.key, group);
  }
  return [...groups.values()].sort((a, b) => b.order - a.order);
}

function sameRule(a: Rule, b: Rule) {
  return (
    a.exposed === b.exposed &&
    [...a.account_types].sort().join("|") ===
      [...b.account_types].sort().join("|")
  );
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${checked ? "bg-stone-900" : "bg-stone-300"}`}
    >
      <span
        className={`absolute top-0.5 left-0.5 size-4 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-4" : ""}`}
      />
    </button>
  );
}

function HaveMark({ has }: { has: boolean }) {
  return has ? (
    <span className="inline-flex size-6 items-center justify-center rounded-md bg-emerald-50 text-xs font-semibold text-emerald-700">
      ✓
    </span>
  ) : (
    <span className="text-stone-300">—</span>
  );
}

function ResultText({
  model,
  rule,
  labels,
}: {
  model: CatalogModel;
  rule: Rule;
  labels: Record<string, string>;
}) {
  if (!rule.exposed) {
    return <span className="text-xs text-stone-400">不对外</span>;
  }
  const servedBy = model.available_types.filter((type) =>
    rule.account_types.includes(type),
  );
  if (servedBy.length === 0 && !model.anonymous) {
    return (
      <span className="text-xs text-rose-600">没有账号能跑，不会出现</span>
    );
  }
  const who = servedBy.length
    ? servedBy.map((type) => labels[type] ?? type).join(" + ")
    : "匿名";
  return <span className="text-xs text-emerald-700">对外，由 {who} 接</span>;
}

function FeatureModelsCard({
  catalog,
  onSaved,
}: {
  catalog: ModelCatalog;
  onSaved: (data: ModelCatalog) => void;
}) {
  const [draft, setDraft] = useState<FeatureModels>(catalog.features);
  const [isSaving, setIsSaving] = useState(false);
  const known = useMemo(
    () => new Set(catalog.models.map((model) => model.id)),
    [catalog.models],
  );
  const dirty = FEATURES.some(({ key }) => draft[key] !== catalog.features[key]);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      onSaved(await saveModelRules({ features: draft }));
      toast.success("已保存，下一次调用就用新模型");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="divide-y divide-stone-100 p-0">
        <div className="px-6 py-5">
          <h2 className="text-sm font-semibold text-stone-800">
            内置功能用哪个模型
          </h2>
          <p className="mt-1 text-xs leading-5 text-stone-500">
            内置功能不受「对外显示」开关影响，但只派给允许接这个模型的账号类型。不在上游列表里的模型（比如
            gpt-5-5）派给任意账号。
          </p>
        </div>
        <div className="grid gap-5 px-6 py-5 md:grid-cols-3">
          {FEATURES.map(({ key, label, help }) => (
            <div key={key} className="space-y-1.5">
              <label className="text-sm font-medium text-stone-700">
                {label}
              </label>
              <Select
                value={draft[key]}
                onValueChange={(value) =>
                  setDraft((prev) => ({ ...prev, [key]: value }))
                }
              >
                <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white font-mono text-[13px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {catalog.feature_options.map((option) => (
                    <SelectItem
                      key={option}
                      value={option}
                      className="font-mono text-[13px]"
                    >
                      {shortId(option)}
                      {option === catalog.feature_defaults[key]
                        ? "（默认）"
                        : ""}
                      {known.has(option) ? "" : "（不在上游列表）"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="text-xs leading-5 text-stone-400">{help}</div>
            </div>
          ))}
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
              onClick={() => setDraft(catalog.features)}
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

function ModelsContent() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [draft, setDraft] = useState<Record<string, Rule>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const applyCatalog = useCallback((data: ModelCatalog) => {
    setCatalog(data);
    setDraft(
      Object.fromEntries(
        data.models.map((model) => [
          model.id,
          { exposed: model.exposed, account_types: model.account_types },
        ]),
      ),
    );
  }, []);

  useEffect(() => {
    let active = true;
    void fetchModelCatalog()
      .then((data) => {
        if (active) {
          applyCatalog(data);
        }
      })
      .catch((error) =>
        toast.error(error instanceof Error ? error.message : "加载模型失败"),
      )
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [applyCatalog]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      applyCatalog(await refreshModelCatalog());
      toast.success("已重新拉取各账号类型的模型列表");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "刷新失败");
    } finally {
      setIsRefreshing(false);
    }
  };

  const changed = useMemo(() => {
    if (!catalog) {
      return {} as Record<string, ModelRuleChange>;
    }
    const result: Record<string, ModelRuleChange> = {};
    for (const model of catalog.models) {
      const rule = draft[model.id];
      if (
        rule &&
        !sameRule(rule, {
          exposed: model.exposed,
          account_types: model.account_types,
        })
      ) {
        result[model.id] = rule;
      }
    }
    return result;
  }, [catalog, draft]);
  const dirtyCount = Object.keys(changed).length;

  const handleSave = async () => {
    setIsSaving(true);
    try {
      applyCatalog(await saveModelRules({ models: changed }));
      toast.success(`已保存 ${dirtyCount} 个模型的规则`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  const handlePolicy = async (policy: ModelCatalog["default_policy"]) => {
    setIsSaving(true);
    try {
      applyCatalog(await saveModelRules({ default_policy: policy }));
      toast.success(`默认规则改为：${POLICY_TEXT[policy]}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  const setRule = (id: string, update: (rule: Rule) => Rule) =>
    setDraft((prev) => ({ ...prev, [id]: update(prev[id]) }));

  const types = catalog?.account_types ?? [];
  const labels = Object.fromEntries(types.map((item) => [item.type, item.label]));

  const counts = useMemo(() => {
    let shown = 0;
    let noAccount = 0;
    let hidden = 0;
    for (const model of catalog?.models ?? []) {
      const rule = draft[model.id];
      if (!rule) {
        continue;
      }
      if (!rule.exposed) {
        hidden += 1;
      } else if (
        model.anonymous ||
        model.available_types.some((type) => rule.account_types.includes(type))
      ) {
        shown += 1;
      } else {
        noAccount += 1;
      }
    }
    return { shown, noAccount, hidden };
  }, [catalog, draft]);

  const warningCount = catalog?.warnings.length ?? 0;

  return (
    <section className="space-y-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">模型管理</h1>
          <p className="text-sm text-stone-500">
            {catalog ? (
              <>
                对外显示 {counts.shown} 个模型，{counts.noAccount}{" "}
                个打开了却没有账号能跑，{counts.hidden} 个不对外。上次拉取{" "}
                {timeText(catalog.refreshed_at)}，每{" "}
                {Math.round(catalog.cache_ttl_seconds / 60)} 分钟自动重新拉取。
              </>
            ) : (
              "正在加载…"
            )}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => void handleRefresh()}
            disabled={isRefreshing || isLoading}
            title="马上向每类账号重新拉取一次模型列表"
            className="h-10 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
          >
            <RefreshCw
              className={`size-4 ${isRefreshing ? "animate-spin" : ""}`}
            />
            重新拉取
          </Button>
        </div>
      </div>

      {catalog ? (
        <div className="flex flex-wrap gap-2 text-xs text-stone-600">
          {types.map((item) => (
            <div
              key={item.type}
              className="rounded-xl border border-stone-200 bg-white px-3 py-2"
            >
              <span className="font-medium text-stone-800">{item.label}</span>{" "}
              · {item.accounts} 个可用账号 · {item.models.length} 个模型 ·
              拉取于 {timeText(item.fetched_at)}
              {item.failed ? (
                <span className="ml-1 text-rose-600">（上次拉取失败）</span>
              ) : null}
            </div>
          ))}
          <div className="rounded-xl border border-stone-200 bg-white px-3 py-2">
            <span className="font-medium text-stone-800">匿名</span> ·{" "}
            {catalog.anonymous.models.length} 个模型
            {catalog.anonymous.failed ? (
              <span className="ml-1 text-rose-600">（上次拉取失败）</span>
            ) : null}
          </div>
          {catalog.image_models?.length ? (
            <div
              className="rounded-xl border border-stone-200 bg-white px-3 py-2"
              title="图片模型不在下表里，有账号就对外。名字只是入口，实际出图用的是 ChatGPT 网页当时的图片模型。"
            >
              <span className="font-medium text-stone-800">图片</span> ·{" "}
              <span className="font-mono">{catalog.image_models.join("、")}</span> · 总是对外
            </div>
          ) : null}
        </div>
      ) : null}

      {warningCount ? (
        <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>
            有 {warningCount}{" "}
            个模型上次调用被上游拒绝（403）：账号类型列出了它，但实际跑不了。可以在下表把这个类型取消勾选。下次调用成功后提示自动消失。
          </span>
        </div>
      ) : null}

      <Card className="overflow-hidden rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          {isLoading && !catalog ? (
            <div className="flex items-center justify-center py-14">
              <LoaderCircle className="size-5 animate-spin text-stone-400" />
            </div>
          ) : null}
          {catalog && catalog.models.length === 0 ? (
            <div className="px-6 py-14 text-center text-sm text-stone-500">
              还没有拉到任何模型。确认号池里有可用账号后点「重新拉取」。
            </div>
          ) : null}
          {catalog && catalog.models.length > 0 ? (
            <div className="overflow-x-auto">
              <Table className="min-w-[980px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-72">模型</TableHead>
                    <TableHead className="w-16">方式</TableHead>
                    {types.map((item) => (
                      <TableHead key={item.type} className="w-16 text-center">
                        {item.label}
                      </TableHead>
                    ))}
                    <TableHead className="w-16 text-center">匿名</TableHead>
                    <TableHead className="w-20">对外显示</TableHead>
                    <TableHead className="w-48">允许接的账号类型</TableHead>
                    <TableHead>结果</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {groupModels(catalog.models).map((group) => [
                    <TableRow key={`group-${group.label}`} className="hover:bg-transparent">
                      <TableCell
                        colSpan={types.length + 6}
                        className="bg-stone-50 py-1.5 text-xs tracking-wide text-stone-500"
                      >
                        {group.label}
                      </TableCell>
                    </TableRow>,
                    ...group.items.map((model) => {
                      const rule = draft[model.id] ?? {
                        exposed: model.exposed,
                        account_types: model.account_types,
                      };
                      const isChanged = Boolean(changed[model.id]);
                      return (
                        <TableRow
                          key={model.id}
                          className={`text-stone-600 ${isChanged ? "bg-amber-50/60" : ""}`}
                        >
                          <TableCell className="max-w-72">
                            <div
                              className="truncate font-mono text-[13px] text-stone-800"
                              title={model.id}
                            >
                              {shortId(model.id)}
                            </div>
                            <div className="flex flex-wrap items-center gap-1 text-xs text-stone-400">
                              <span className="truncate">{model.title}</span>
                              {model.tags.includes("hidden") ? (
                                <Badge
                                  variant="secondary"
                                  className="rounded-md px-1.5 py-0 text-[11px]"
                                >
                                  上游隐藏
                                </Badge>
                              ) : null}
                              {model.rule_source === "admin" ? (
                                <Badge
                                  variant="outline"
                                  className="rounded-md px-1.5 py-0 text-[11px] text-stone-500"
                                >
                                  已手动设置
                                </Badge>
                              ) : null}
                            </div>
                          </TableCell>
                          <TableCell className="text-xs text-stone-500">
                            {REASONING_LABELS[model.reasoning_type] ??
                              (model.reasoning_type || "—")}
                          </TableCell>
                          {types.map((item) => (
                            <TableCell key={item.type} className="text-center">
                              <HaveMark
                                has={model.available_types.includes(item.type)}
                              />
                            </TableCell>
                          ))}
                          <TableCell className="text-center">
                            <HaveMark has={model.anonymous} />
                          </TableCell>
                          <TableCell>
                            <Toggle
                              checked={rule.exposed}
                              label={`对外显示 ${model.id}`}
                              onChange={(value) =>
                                setRule(model.id, (current) => ({
                                  ...current,
                                  exposed: value,
                                }))
                              }
                            />
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-wrap gap-1.5">
                              {types.map((item) => {
                                const has = model.available_types.includes(
                                  item.type,
                                );
                                const pressed =
                                  has && rule.account_types.includes(item.type);
                                return (
                                  <button
                                    key={item.type}
                                    type="button"
                                    aria-pressed={pressed}
                                    disabled={!has}
                                    title={
                                      has
                                        ? `${pressed ? "不再" : ""}让 ${item.label} 账号接这个模型`
                                        : `${item.label} 账号没有这个模型`
                                    }
                                    onClick={() =>
                                      setRule(model.id, (current) => ({
                                        ...current,
                                        account_types: current.account_types.includes(
                                          item.type,
                                        )
                                          ? current.account_types.filter(
                                              (type) => type !== item.type,
                                            )
                                          : [...current.account_types, item.type],
                                      }))
                                    }
                                    className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
                                      !has
                                        ? "cursor-default border-stone-100 text-stone-300 line-through"
                                        : pressed
                                          ? "border-stone-900 bg-stone-900 text-white"
                                          : "border-stone-200 bg-white text-stone-500 hover:border-stone-400"
                                    }`}
                                  >
                                    {item.label}
                                  </button>
                                );
                              })}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="space-y-1">
                              <ResultText
                                model={model}
                                rule={rule}
                                labels={labels}
                              />
                              {model.warnings.map((warning) => (
                                <div
                                  key={warning.account_type}
                                  title={warning.detail || undefined}
                                  className="flex items-center gap-1 text-xs text-amber-700"
                                >
                                  <AlertTriangle className="size-3.5" />
                                  {labels[warning.account_type] ??
                                    (warning.account_type === "anonymous"
                                      ? "匿名"
                                      : warning.account_type)}{" "}
                                  上次调用 403 · {timeText(warning.at)}
                                </div>
                              ))}
                            </div>
                          </TableCell>
                        </TableRow>
                      );
                    }),
                  ])}
                </TableBody>
              </Table>
            </div>
          ) : null}
          {catalog ? (
            <div className="flex flex-wrap items-center gap-2 border-t border-stone-100 px-5 py-4">
              <span
                className={`mr-auto flex flex-wrap items-center gap-2 text-xs ${dirtyCount ? "text-amber-600" : "text-stone-400"}`}
              >
                {dirtyCount ? (
                  `${dirtyCount} 个模型有未保存的修改`
                ) : (
                  <>
                    上游新出现的模型：
                    <Select
                      value={catalog.default_policy}
                      onValueChange={(value) =>
                        void handlePolicy(value as ModelCatalog["default_policy"])
                      }
                      disabled={isSaving}
                    >
                      <SelectTrigger className="h-8 w-auto rounded-lg border-stone-200 bg-white text-xs text-stone-700">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(Object.keys(POLICY_TEXT) as ModelCatalog["default_policy"][]).map((policy) => (
                          <SelectItem key={policy} value={policy}>
                            {POLICY_TEXT[policy]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    换规则会重算你没手动改过的模型。请求被关掉或没有账号能跑的模型会直接报错，不会换成别的模型。
                  </>
                )}
              </span>
              {dirtyCount ? (
                <Button
                  variant="ghost"
                  className="h-9 rounded-xl px-3 text-stone-600"
                  onClick={() => applyCatalog(catalog)}
                  disabled={isSaving}
                >
                  <Undo2 className="size-4" />
                  撤销
                </Button>
              ) : null}
              <Button
                className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
                onClick={() => void handleSave()}
                disabled={!dirtyCount || isSaving}
              >
                {isSaving ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Save className="size-4" />
                )}
                保存规则
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {catalog ? (
        <FeatureModelsCard
          key={JSON.stringify(catalog.features)}
          catalog={catalog}
          onSaved={applyCatalog}
        />
      ) : null}
    </section>
  );
}

export default function ModelsPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }
  return <ModelsContent />;
}
