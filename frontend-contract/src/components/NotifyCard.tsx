/** Карточка уведомления сверху на открытой вкладке (контракт v1.5, Ф10): уходит сама через 10 с. */
import { Bell, X } from 'lucide-react';
import { useSyncExternalStore } from 'react';
import { T } from '../content/texts';
import { metaParts } from '../lib/dates';
import { closeNotifyCard, notifyStore, snoozeNotifyCard, viewNotifyCard } from '../state/notify';
import { Pill } from './Pill';

export function NotifyCard() {
  const { card } = useSyncExternalStore(notifyStore.subscribe, notifyStore.get);
  if (!card) return null;
  const { record } = card;
  const when = metaParts(record, true).map((part) => part.text).join(' · ');
  return (
    <div className="notify-card" role="alert" data-tag={card.tag}>
      <button type="button" className="notify-card__close" aria-label={T.notify_close} onClick={closeNotifyCard}>
        <X size={16} strokeWidth={1.5} aria-hidden="true" />
      </button>
      <div className="notify-card__title">
        <Bell size={16} strokeWidth={1.5} aria-hidden="true" />
        {card.title}
      </div>
      <div className="notify-card__text">
        {record.text}
        {when && ` · ${when}`}
      </div>
      <div className="notify-card__actions">
        <Pill variant="primary" onClick={viewNotifyCard}>{T.notify_view}</Pill>
        <Pill variant="secondary" onClick={() => void snoozeNotifyCard()}>{T.notify_snooze}</Pill>
      </div>
    </div>
  );
}
