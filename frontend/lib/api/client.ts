const base = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://127.0.0.1:8000';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

async function parseJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(`Invalid JSON response: ${text.slice(0, 200)}`, res.status);
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    const res = await fetch(`${base}${path}`, init);
    const data = await parseJson<any>(res);
    if (!res.ok) {
      const message =
        typeof data === 'object' && data && 'detail' in data
          ? String((data as { detail: unknown }).detail)
          : `Request failed with status ${res.status}`;
      throw new ApiError(message, res.status);
    }
    return data as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError('Unable to reach the backend. Check that the API is running and CORS allows this origin.');
  }
}

export const apiClient = {
  health: () => request<{ status: string; app_name: string; version: string; environment: string }>('/health/'),
  readiness: () => request<{ status: string; checks: Array<{ name: string; healthy: boolean; detail: string | null }> }>('/health/readiness'),
  listFiles: () => request<{ items: any[]; pagination: any }>('/api/files/'),
  getFile: (id: string) => request<any>(`/api/files/${encodeURIComponent(id)}/`),
  getMeasurements: (id: string, page = 1, pageSize = 50) =>
    request<any>(`/api/files/${encodeURIComponent(id)}/measurements/?page=${page}&page_size=${pageSize}`),
  upload: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<any>('/api/files/', { method: 'POST', body: form });
  },
  deleteFile: (id: string) => request<void>(`/api/files/${encodeURIComponent(id)}/`, { method: 'DELETE' }),
};
