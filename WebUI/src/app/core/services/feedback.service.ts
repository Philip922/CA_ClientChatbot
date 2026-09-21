import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { FeedbackPayload } from '../../features/chat/models/feedback.model';

/**
 * One POST per rating action. No retry — if it fails, the calling component
 * reverts the selected state and lets the user try again by clicking.
 */
@Injectable({ providedIn: 'root' })
export class FeedbackService {
    private readonly http = inject(HttpClient);
    private readonly endpoint = `${environment.apiUrl}/feedback`;

    submit(payload: FeedbackPayload): Observable<void> {
        return this.http.post<void>(this.endpoint, payload);
    }
}
