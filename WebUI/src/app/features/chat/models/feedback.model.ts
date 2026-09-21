export type FeedbackRating = 'up' | 'down';

export interface FeedbackPayload {
    messageId: string;
    rating: FeedbackRating;
    /** Optional response text, sent so the backend can log rating context. */
    content?: string;
}
