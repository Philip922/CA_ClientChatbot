import {
    ChangeDetectionStrategy,
    Component,
    ElementRef,
    afterRenderEffect,
    computed,
    inject,
    signal,
    untracked,
    viewChild
} from '@angular/core';
import { LucideSquarePen } from '@lucide/angular';
import { ChatService } from '../../core/services/chat.service';
import { InputBarComponent } from './components/input-bar/input-bar.component';
import { MessageBubbleComponent } from './components/message-bubble/message-bubble.component';

/** How close to the bottom the user must be for streaming to keep scrolling. */
const STICK_THRESHOLD_PX = 80;

const SUGGESTED_PROMPTS = [
    'What industries does Cadre work with?',
    'How does Cadre approach an AI engagement?',
    'Can I speak to someone on the team?'
];

@Component({
    selector: 'app-chat',
    standalone: true,
    imports: [InputBarComponent, MessageBubbleComponent, LucideSquarePen],
    templateUrl: './chat.component.html',
    styleUrl: './chat.component.scss',
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class ChatComponent {
    private readonly chat = inject(ChatService);

    protected readonly messages = this.chat.messages;
    protected readonly isStreaming = this.chat.isStreaming;
    protected readonly isEmpty = this.chat.isEmpty;
    protected readonly suggestions = SUGGESTED_PROMPTS;

    protected readonly status = computed(() => (this.isStreaming() ? 'Thinking…' : 'Ready'));

    private readonly scroller = viewChild.required<ElementRef<HTMLElement>>('scroller');

    /**
     * Whether new content should pull the view down. Set false as soon as the
     * user scrolls up to read something — a stream that yanks the viewport away
     * mid-sentence is worse than one that quietly continues below the fold.
     */
    private readonly stick = signal(true);

    constructor() {
        // Runs after the DOM is committed, so the new token's height is already
        // reflected in scrollHeight.
        afterRenderEffect(() => {
            this.messages();
            if (untracked(this.stick)) this.scrollToBottom();
        });
    }

    protected onScroll(): void {
        const el = this.scroller().nativeElement;
        const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
        this.stick.set(distance < STICK_THRESHOLD_PX);
    }

    protected onSend(text: string): void {
        this.stick.set(true);
        void this.chat.sendMessage(text);
    }

    protected onRetry(): void {
        this.stick.set(true);
        void this.chat.retry();
    }

    /** Drops the conversation, aborting any reply still streaming. */
    protected onNewChat(): void {
        this.stick.set(true);
        this.chat.clear();
    }

    private scrollToBottom(): void {
        const el = this.scroller().nativeElement;
        el.scrollTop = el.scrollHeight;
    }
}
