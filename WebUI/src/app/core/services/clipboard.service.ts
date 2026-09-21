import { Injectable } from '@angular/core';

/**
 * Thin wrapper over the async clipboard API. Rejects on permission denial or on
 * an insecure origin, so callers can distinguish "copied" from "failed" and show
 * the right icon state.
 */
@Injectable({ providedIn: 'root' })
export class ClipboardService {
    async copy(text: string): Promise<void> {
        if (!navigator.clipboard?.writeText) {
            throw new Error('Clipboard API unavailable');
        }
        await navigator.clipboard.writeText(text);
    }
}
