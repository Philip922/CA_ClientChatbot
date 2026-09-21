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
    readonly close = output<void>();

    protected readonly domain = sourceDomain;

    private readonly excerpts = viewChildren<ElementRef<HTMLElement>>('excerpt');

    /** Indices whose excerpt is clipped by the 3-line clamp. */
    private readonly overflowing = signal<ReadonlySet<number>>(new Set());
    private readonly expanded = signal<ReadonlySet<number>>(new Set());

    constructor() {
        // The list is fixed for the lifetime of the panel (it only opens on
        // completed messages), so one measurement pass after render is enough.
        afterNextRender(() => {
            const clipped = new Set<number>();
            this.excerpts().forEach((ref, index) => {
                const el = ref.nativeElement;
                if (el.scrollHeight - el.clientHeight > 1) clipped.add(index);
            });
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
        next.has(index) ? next.delete(index) : next.add(index);
        this.expanded.set(next);
    }

    protected onEscape(): void {
        this.close.emit();
    }
}
