import {
    ChangeDetectionStrategy,
    Component,
    DestroyRef,
    ElementRef,
    computed,
    inject,
    input,
    signal
} from '@angular/core';
import { LucideCheck, LucideCopy, LucideThumbsDown, LucideThumbsUp } from '@lucide/angular';
import { ChatService } from '../../../../core/services/chat.service';
import { ClipboardService } from '../../../../core/services/clipboard.service';
import { FeedbackService } from '../../../../core/services/feedback.service';
import { Message, messageSources } from '../../models/message.model';
import { FeedbackRating } from '../../models/feedback.model';
import { SourcesPanelComponent } from '../sources-panel/sources-panel.component';

const COPY_CONFIRM_MS = 2000;

/**
 * Copy, rate, and inspect sources. Rendered under completed agent messages only.
 */
@Component({
    selector: 'app-message-actions',
    standalone: true,
    imports: [LucideCheck, LucideCopy, LucideThumbsDown, LucideThumbsUp, SourcesPanelComponent],
    templateUrl: './message-actions.component.html',
    styleUrl: './message-actions.component.scss',
    changeDetection: ChangeDetectionStrategy.OnPush,
    host: {
        '(document:click)': 'onDocumentClick($event)',
        '(document:keydown.escape)': 'closeSources()'
    }
})
export class MessageActionsComponent {
    readonly message = input.required<Message>();

    private readonly chat = inject(ChatService);
    private readonly clipboard = inject(ClipboardService);
    private readonly feedback = inject(FeedbackService);
    private readonly host = inject(ElementRef<HTMLElement>);
    private readonly destroyRef = inject(DestroyRef);

    protected readonly sources = computed(() => messageSources(this.message()));
    protected readonly rating = computed(() => this.message().feedback);

    protected readonly copied = signal(false);
    protected readonly copyFailed = signal(false);
    protected readonly sourcesOpen = signal(false);
    protected readonly submitting = signal(false);
    protected readonly feedbackFailed = signal(false);

    /** One rating per message: locked once a submission succeeds. */
    protected readonly ratingLocked = computed(() => this.rating() !== null || this.submitting());

    private copyTimer: ReturnType<typeof setTimeout> | null = null;

    constructor() {
        this.destroyRef.onDestroy(() => {
            if (this.copyTimer) clearTimeout(this.copyTimer);
        });
    }

    protected async copy(): Promise<void> {
        if (this.copyTimer) clearTimeout(this.copyTimer);
        this.copyFailed.set(false);

        try {
            await this.clipboard.copy(this.message().content);
            this.copied.set(true);
            this.copyTimer = setTimeout(() => this.copied.set(false), COPY_CONFIRM_MS);
        } catch {
            this.copied.set(false);
            this.copyFailed.set(true);
            this.copyTimer = setTimeout(() => this.copyFailed.set(false), COPY_CONFIRM_MS);
        }
    }

    protected rate(value: FeedbackRating): void {
        if (this.ratingLocked()) return;

        const id = this.message().id;
        // Optimistic: the button reads as selected immediately, and reverts if
        // the POST fails. A rating is cheap to redo, so there is no retry logic.
        this.chat.setFeedback(id, value);
        this.submitting.set(true);
        this.feedbackFailed.set(false);

        this.feedback.submit({ messageId: id, rating: value, content: this.message().content }).subscribe({
            next: () => this.submitting.set(false),
            error: () => {
                this.chat.setFeedback(id, null);
                this.submitting.set(false);
                this.feedbackFailed.set(true);
            }
        });
    }

    protected toggleSources(): void {
        this.sourcesOpen.update(open => !open);
    }

    protected closeSources(): void {
        this.sourcesOpen.set(false);
    }

    protected onDocumentClick(event: MouseEvent): void {
        if (!this.sourcesOpen()) return;
        if (!this.host.nativeElement.contains(event.target as Node)) this.closeSources();
    }
}
