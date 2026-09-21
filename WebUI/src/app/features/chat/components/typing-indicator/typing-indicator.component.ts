import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * Three dots shown between the user's message and the first arriving token.
 * The animation is dropped under `prefers-reduced-motion` — see the stylesheet.
 */
@Component({
    selector: 'app-typing-indicator',
    standalone: true,
    templateUrl: './typing-indicator.component.html',
    styleUrl: './typing-indicator.component.scss',
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class TypingIndicatorComponent {}
