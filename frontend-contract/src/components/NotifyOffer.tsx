/** Плашка под ответом created: «Прислать уведомление в 10:00?» — один раз, пока человек не ответил. */
import { useSyncExternalStore } from 'react';
import { T, fill } from '../content/texts';
import { emitActivity } from '../state/events';
import { acceptOffer, declineOffer, notifyStore } from '../state/notify';
import { Pill } from './Pill';

export function NotifyOffer({ messageId }: { messageId: string }) {
  const { offer } = useSyncExternalStore(notifyStore.subscribe, notifyStore.get);
  if (!offer || offer.messageId !== messageId) return null;
  return (
    <div className="notify-offer" role="group" aria-label={fill(T.notify_title, { time: offer.time })}>
      <div className="notify-offer__title">{fill(T.notify_title, { time: offer.time })}</div>
      <div className="notify-offer__text">{T.notify_text}</div>
      <div className="notify-offer__actions">
        <Pill variant="primary" onClick={() => { emitActivity('button-press'); void acceptOffer(); }}>{T.notify_yes}</Pill>
        <Pill variant="secondary" onClick={() => { emitActivity('button-press'); declineOffer(); }}>{T.notify_no}</Pill>
      </div>
    </div>
  );
}
