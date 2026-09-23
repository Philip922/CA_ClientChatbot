import {
    ChangeDetectionStrategy,
    Component,
    ElementRef,
    afterNextRender,
    input,
    output,
    signal,
    viewChildren
} from '@angular/core';
import { LucideFileText, LucideLink } from '@lucide/angular';
import { Source, sourceDomain } from '../../models/source.model';

/**
 * Inline popover listing the sources behind a message.
 *
 * Deliberately not a modal: it sits in the document flow directly below the
 * actions row so the answer stays readable alongside its citations.
 */
@Component({
    selector: 'app-sources-panel',
    standalone: true,
    imports: [LucideFileText, LucideLink],
    templateUrl: './sources-panel.component.html',
    styleUrl: './sources-panel.component.scss',
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class SourcesPanelComponent {
    readonly sources = input.required<Source[]>();
    /** Not `close`: that is a native DOM event name and would shadow it on the host. */
    readonly closed = output<void>();

    protected readonly domain = sourceDomain;

    private readonly excerpts = viewChildren<ElementRef<HTMLElement>>('excerpt');

    /** Indices whose excerpt is clipped by the 3-line clamp. */
    private readonly overflowing = signal<ReadonlySet<number>>(new Set());
    private readonly expanded = signal<ReadonlySet<number>>(new Set());

    constructor() {
        // The list is fixed for the lifetime of the panel (it only opens on
        // completed messages), so one measurement pass after render is enough.
        afterNextRender(() => {
            // Only documents with an excerpt render one, so a position in
            // excerpts() is not a source index; each element carries its own.
            const clipped = new Set<number>();
            for (const ref of this.excerpts()) {
                const el = ref.nativeElement;
                if (el.scrollHeight - el.clientHeight > 1) clipped.add(Number(el.dataset['index']));
            }
            this.overflowing.set(clipped);
        });
    }

    protected isExpanded(index: number): boolean {
        return this.expanded().has(index);
    }

    protected canExpand(index: number): boolean {
        return this.overflowing().has(index);
    }

    protected toggle(index: number): void {
        const next = new Set(this.expanded());
        if (!next.delete(index)) next.add(index);
        this.expanded.set(next);
    }

    protected onEscape(): void {
        this.closed.emit();
    }
}
