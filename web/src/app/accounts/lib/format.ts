import type { Account, AccountStatus } from "@/lib/api";

export type TestStatus = NonNullable<Account["chat_test_status"]>;

export const STATUS_ORDER: AccountStatus[] = ["正常", "限流", "异常", "禁用"];

export const statusTone: Record<AccountStatus, string> = {
  正常: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  限流: "bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  异常: "bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
  禁用: "bg-stone-100 text-stone-500",
};

export const typeTone: Record<string, string> = {
  plus: "text-violet-600 dark:text-violet-300",
  pro: "text-violet-600 dark:text-violet-300",
  team: "text-sky-600 dark:text-sky-300",
};

const testMeta: Record<TestStatus | "running", { dot: string; text: string }> = {
  可用: { dot: "bg-emerald-500", text: "可用" },
  不可用: { dot: "bg-rose-500", text: "不可用" },
  测试失败: { dot: "bg-amber-500", text: "未知" },
  未测试: { dot: "bg-stone-300 dark:bg-stone-600", text: "未测" },
  running: { dot: "bg-sky-500 animate-pulse", text: "检测中" },
};

export function testChipMeta(status: TestStatus | undefined, running: boolean) {
  return testMeta[running ? "running" : status || "未测试"] ?? testMeta["未测试"];
}

const featureNames: Record<string, string> = {
  image_gen: "图片生成",
  deep_research: "深度研究",
  file_upload: "文件上传",
  paste_text_to_file: "长文本转文件",
  reason: "推理",
};

export function featureLabel(name?: string) {
  if (!name) return "—";
  return featureNames[name] ?? name;
}

// Backend times are either ISO with offset or "YYYY-MM-DD HH:MM:SS" in UTC without a zone.
export function parseTime(value?: string | null): Date | null {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  let text = raw.includes("T") ? raw : raw.replace(" ", "T");
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(text)) text += "Z";
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

function spanText(minutes: number) {
  if (minutes < 60) return `${minutes} 分钟`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} 小时`;
  return `${Math.round(minutes / 1440)} 天`;
}

export function ago(value?: string | null) {
  const date = parseTime(value);
  if (!date) return "—";
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "刚刚";
  return `${spanText(minutes)}前`;
}

export function until(value?: string | null) {
  const date = parseTime(value);
  if (!date) return "";
  const minutes = Math.round((date.getTime() - Date.now()) / 60000);
  if (minutes <= 0) return "已可恢复";
  return `${spanText(minutes)}后恢复`;
}

export function untilPlain(value?: string | null) {
  const date = parseTime(value);
  if (!date) return "—";
  const minutes = Math.round((date.getTime() - Date.now()) / 60000);
  if (minutes <= 0) return "已过期";
  return `${spanText(minutes)}后`;
}

export function fullTime(value?: string | null) {
  const date = parseTime(value);
  return date ? date.toLocaleString("zh-CN", { hour12: false }) : "—";
}

export function shortToken(token?: string) {
  if (!token) return "—";
  return token.length <= 16 ? token : `${token.slice(0, 6)}…${token.slice(-6)}`;
}

export function accountType(account: Account) {
  return String(account.type || "free").toLowerCase();
}

export function copyText(text: string) {
  return navigator.clipboard.writeText(text);
}

export function downloadTokens(accounts: Account[]) {
  const content = `${accounts.map((account) => account.access_token).join("\n")}\n`;
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `accounts-${Date.now()}.txt`;
  link.click();
  URL.revokeObjectURL(url);
}
