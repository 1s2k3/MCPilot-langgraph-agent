/** 统一 fetch 封装：错误信封 → 抛错；ADMIN_TOKEN 鉴权；JSON 解析。 */

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    details: Record<string, unknown>;
  };
}

export class ApiError extends Error {
  code: string;
  retryable: boolean;

  constructor(code: string, message: string, retryable: boolean) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function getAdminToken(): string | null {
  return localStorage.getItem("admin_token");
}

export function buildHeaders(extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((extra as Record<string, string>) ?? {}),
  };
  const token = getAdminToken();
  if (token) headers["X-Admin-Token"] = token;
  return headers;
}

/** SSE 用：EventSource 无法自定义头 → admin_token 走查询参数（后端双通道校验）。 */
export function adminQueryParam(url: string): string {
  const token = getAdminToken();
  if (!token) return url;
  return `${url}${url.includes("?") ? "&" : "?"}admin_token=${encodeURIComponent(token)}`;
}

async function doFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(path, { headers: buildHeaders(init?.headers), ...init });
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res = await doFetch(path, init);
  // 401：未携带令牌时提示输入（配置了 ADMIN_TOKEN 的部署）
  if (res.status === 401 && !getAdminToken()) {
    const token = window.prompt("此部署需要管理令牌（ADMIN_TOKEN），请输入：");
    if (token) {
      localStorage.setItem("admin_token", token);
      res = await doFetch(path, init);
    }
  }
  if (!res.ok) {
    let envelope: ApiErrorEnvelope | null = null;
    try {
      envelope = (await res.json()) as ApiErrorEnvelope;
    } catch {
      /* 非 JSON 响应 */
    }
    throw new ApiError(
      envelope?.error.code ?? "http_error",
      envelope?.error.message ?? `请求失败 (${res.status})`,
      envelope?.error.retryable ?? false,
    );
  }
  return (await res.json()) as T;
}
