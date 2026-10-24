/** Д15 Обучение в песочнице — панель над строкой ввода. 07-extras.md, раздел 11. */
import { Check } from 'lucide-react';
import { useSyncExternalStore } from 'react';
import { Pill } from '../../components/Pill';
import { T, fill } from '../../content/texts';
import { resetSandbox } from '../../state/chat';
import { emitActivity } from '../../state/events';
import { useApp } from '../../state/store';
import { closeTour, declineTour, hintReady, skipStep, startTour, tourStore, tourSteps, sendHint } from './state';
import { notifyStore } from '../../state/notify';
import './tour.css';


const press = (fn: () => void) => () => { emitActivity('button-press'); fn(); };

export function TourBar() {
  const t = useSyncExternalStore(tourStore.subscribe, tourStore.get);
  const busy = useApp((s) => s.waiting || s.recording);
  // Перерисовка при новом ответе: кнопка-подсказка активна, только когда нужная кнопка есть под ответом
  useApp((s) => s.messages.length);
  // «Посмотреть» в шаге «Уведомление» активна, пока карточка на экране
  useSyncExternalStore(notifyStore.subscribe, () => notifyStore.get().card);
  const steps = tourSteps();

  if (t.status === 'offer') {
    return (
      <div className="tour" role="group" aria-label={T.tour_label}>
        <div className="tour__title">{t.again ? T.tour_offer_again_title : T.tour_offer_title}</div>
        <div className="tour__text">{fill(T.tour_offer_text, { N: steps.length })}</div>
        <div className="tour__actions">
          <Pill variant="primary" onClick={press(() => void startTour())}>{T.tour_offer_yes}</Pill>
          <Pill variant="secondary" onClick={press(declineTour)}>{T.tour_offer_no}</Pill>
        </div>
      </div>
    );
  }

  if (t.status === 'done') {
    return (
      <div className="tour" role="group" aria-label={T.tour_label}>
        <div className="tour__title">{T.tour_done_title}</div>
        <div className="tour__text">{T.tour_done_text}</div>
        <div className="tour__text">{T.tour_done_more}</div>
        <div className="tour__actions">
          <Pill variant="primary" onClick={press(() => { closeTour(); void resetSandbox(); })}>{T.tour_done_reset}</Pill>
          <Pill variant="secondary" onClick={press(closeTour)}>{T.tour_done_close}</Pill>
        </div>
      </div>
    );
  }

  if (t.status !== 'running') return null;
  const step = steps[t.step];
  if (!step) return null;
  // Подсказок бывает меньше, чем ответов шага (календарь: открыть → перетащить) — держим последнюю
  const hint = step.subs[Math.min(t.sub, step.subs.length - 1)];
  const isPress = 'press' in hint;
  const phrase = isPress ? hint.press : hint.say;
  const canPress = !busy && (!isPress || hintReady(hint.press));

  return (
    <div className="tour" role="group" aria-label={T.tour_label}>
      <div className="tour__head">
        <span className="tour__progress">{fill(T.tour_progress, { n: t.step + 1, total: steps.length })}</span>
        <span className="tour__dots" aria-hidden="true">
          {steps.map((_, i) => (
            <span key={i} className={`tour__dot${i < t.step || (i === t.step && t.stepDone) ? ' tour__dot--done' : ''}${i === t.step ? ' tour__dot--now' : ''}`} />
          ))}
        </span>
      </div>
      <div className="tour__title">{step.title}</div>
      {t.stepDone ? (
        <div className="tour__done" role="status">
          <Check size={16} strokeWidth={1.5} aria-hidden="true" />
          {T.tour_step_done}
        </div>
      ) : (
        <>
          <div className="tour__text">{step.task}</div>
          <div className="tour__hint">
            <span className="tour__label">{isPress ? T.tour_press : T.tour_say}</span>
            <Pill variant={canPress ? 'primary' : 'dimmed'} className="tour__phrase" disabled={!canPress} onClick={sendHint}>
              {isPress ? phrase : `«${phrase}»`}
            </Pill>
          </div>
          <div className="tour__actions">
            <Pill variant="secondary" onClick={press(skipStep)}>{T.tour_skip}</Pill>
            <Pill variant="secondary" onClick={press(closeTour)}>{T.tour_exit}</Pill>
          </div>
        </>
      )}
    </div>
  );
}
