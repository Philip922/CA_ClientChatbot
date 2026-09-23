import {
    ChangeDetectionStrategy,
    Component,
    ElementRef,
    computed,
    effect,
    input,
    output,
    signal,
    viewChild
} from '@angular/core';
import { LucideArrowUp, LucideSquare } from '@lucide/angular';
import { MAX_MESSAGE_CHARS } from '../../models/message.model';

/** Lines of text shown before the textarea starts scrolling internally. */
const MAX_ROWS = 4;

/** The character counter only appears once the draft is this close to the limit. */
const COUNTER_THRESHOLD = MAX_MESSAGE_CHARS - 400;

/**
 * Auto-growing composer. Owns only the draft text — the message list lives in
 * `ChatService`, so this component can be dropped anywhere in the page.
 */
@Component({
    selector: 'app-input-bar',
    standalone: true,
    imports: [LucideArrowUp, LucideSquare],
    templateUrl: './input-bar.component.html',
    styleUrl: './input-bar.component.scss',
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class InputBarComponent {
    readonly disabled = input(false);
    readonly placeholder = input('Ask anything about Cadre AI…');
    /** While true the send button becomes a stop button. */
    readonly streaming = input(false);
    readonly send = output<string>();
    readonly stop = output<void>();

    protected readonly maxLength = MAX_MESSAGE_CHARS;
    protected readonly value = signal('');
    protected readonly showCounter = computed(() => this.value().length >= COUNTER_THRESHOLD);
    protected readonly canSend = computed(() => !this.disabled() && this.value().trim().length > 0);

    private readonly textarea = viewChild.required<ElementRef<HTMLTextAreaElement>>('textarea');

    constructor() {
        // Hand focus back as soon as the agent finishes, so a follow-up question
        // needs no click.
        effect(() => {
            if (!this.disabled()) this.textarea().nativeElement.focus();
        });
    }

    protected onInput(event: Event): void {
        this.value.set((event.target as HTMLTextAreaElement).value);
        this.resize();
    }

    /** Enter sends; Shift+Enter inserts a newline and falls through to the textarea. */
    protected onKeydown(event: KeyboardEvent): void {
        if (event.key !== 'Enter' || event.shiftKey) return;
        // IME composition also reports Enter — committing a candidate must not send.
        if (event.isComposing) return;
        event.preventDefault();
        this.submit();
    }

    protected submit(): void {
        if (!this.canSend()) return;
        this.send.emit(this.value().trim());
        this.value.set('');
        this.resize();
    }

    /** Grows the textarea with its content, up to MAX_ROWS. */
    private resize(): void {
        const el = this.textarea().nativeElement;
        el.style.height = 'auto';

        const styles = getComputedStyle(el);
        const lineHeight = parseFloat(styles.lineHeight) || 20;
        const frame =
            parseFloat(styles.paddingTop) +
            parseFloat(styles.paddingBottom) +
            parseFloat(styles.borderTopWidth) +
            parseFloat(styles.borderBottomWidth);
        const max = lineHeight * MAX_ROWS + frame;

        el.style.height = `${Math.min(el.scrollHeight, max)}px`;
        el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden';
    }
}
