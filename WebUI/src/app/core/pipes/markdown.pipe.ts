import { Pipe, PipeTransform, inject, SecurityContext, DOCUMENT } from '@angular/core';
import { DomSanitizer } from '@angular/platform-browser';
import { marked } from 'marked';

/**
 * Renders agent markdown to sanitized HTML.
 *
 * Pure, so Angular only re-runs it when the accumulated content string actually
 * changes — one parse per token rather than one per change detection cycle.
 */
@Pipe({
    name: 'markdown',
    standalone: true
})
export class MarkdownPipe implements PipeTransform {
    private readonly sanitizer = inject(DomSanitizer);
    private readonly document = inject(DOCUMENT);

    transform(value: string): string {
        if (!value) return '';
        const raw = marked.parse(value, { async: false, breaks: true, gfm: true }) as string;
        const safe = this.sanitizer.sanitize(SecurityContext.HTML, raw) ?? '';
        return this.openLinksInNewTab(safe);
    }

    /**
     * The conversation lives only in memory, so a link that navigates this tab
     * away throws it away. Every link in an answer opens in a new tab instead.
     * Done on the parsed DOM rather than with a regex so raw `<a>` tags the
     * model writes itself are caught too. `target` and `rel` are both on
     * Angular's sanitizer allowlist, so they survive the `[innerHTML]` binding.
     */
    private openLinksInNewTab(html: string): string {
        if (!html.includes('<a')) return html;

        // <template> content is inert: nothing in it loads or runs.
        const template = this.document.createElement('template');
        template.innerHTML = html;
        for (const link of Array.from(template.content.querySelectorAll('a[href]'))) {
            link.setAttribute('target', '_blank');
            link.setAttribute('rel', 'noopener noreferrer');
        }
        return template.innerHTML;
    }
}
