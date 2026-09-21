import { Pipe, PipeTransform, inject, SecurityContext } from '@angular/core';
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

    transform(value: string): string {
        if (!value) return '';
        const raw = marked.parse(value, { async: false, breaks: true, gfm: true }) as string;
        return this.sanitizer.sanitize(SecurityContext.HTML, raw) ?? '';
    }
}
