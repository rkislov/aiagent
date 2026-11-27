export const OFFICE_FILE_LIMIT_MIB = 100;

export function validateOfficeFile(file) {
  if (!file || !/\.(docx|xlsx|pptx)$/i.test(file.name) || !file.size) {
    throw new Error('Выберите непустой файл DOCX, XLSX или PPTX.');
  }
  if (file.size > OFFICE_FILE_LIMIT_MIB * 1024 * 1024) {
    throw new Error(`Файл больше ${OFFICE_FILE_LIMIT_MIB} МиБ. Разделите документ или уменьшите размер вложенных изображений.`);
  }
}

export async function uploadOfficeFile(file, signal) {
  validateOfficeFile(file);
  const response = await fetch('/api/files', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/octet-stream',
      'X-Office-Filename': encodeURIComponent(file.name),
    },
    body: file,
    signal,
  });
  if (response.ok) return response.json();
  const data = await response.json().catch(() => null);
  if (typeof data?.detail === 'string') throw new Error(data.detail);
  if (response.status === 413) throw new Error('Сервер отклонил размер файла. Разделите документ на части.');
  throw new Error(`Не удалось загрузить документ (HTTP ${response.status}).`);
}
