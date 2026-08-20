/** 统一 fetch 封装：错误信封 → 抛错；JSON 解析。 */

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

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
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
