// Escape text for safe interpolation into HTML strings (clipboard exports).
// Legal documents commonly contain characters like < > & which would
// otherwise break or alter the generated markup.
export const escapeHtml = (text: string): string =>
  text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
