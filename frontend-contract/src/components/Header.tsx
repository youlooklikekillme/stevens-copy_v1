/** Шапка — 02-screens.md, 2.1. */
import { ChevronDown, Plus } from 'lucide-react';
import { useState, useSyncExternalStore } from 'react';
import { features } from '../config/features';
import { serverHas } from '../config/server';
import { T } from '../content/texts';
import { HeaderRightStart, sandboxMenuExtra } from '../extensions/points';
import { install, installHintKey, isInstalled, useCanInstall, useMedia, useTitlebarMode } from '../lib/install';
import { newDialog, resetSandbox, setHint } from '../state/chat';
import { emitActivity } from '../state/events';
import { disableNotify, enableNotify, notifyStore } from '../state/notify';
import { goAuth, logout } from '../state/session';
import { useApp } from '../state/store';
import { Menu } from './Menu';
import { Modal } from './Modal';

export function Header() {
  const mode = useApp((s) => s.mode);
  const login = useApp((s) => s.login);
  const empty = useApp((s) => s.messages.length === 0);
  const busy = useApp((s) => s.waiting || s.recording);
  const [dialog, setDialog] = useState<'new' | 'reset' | null>(null);
  const canInstall = useCanInstall();
  // В окне приложения справа кнопки окна и значки браузера — свои кнопки ставим слева, «Stevens» остаётся по центру окна
  const titlebar = useTitlebarMode();
  // На телефоне справа тесно: календарь уходит влево к «Новому диалогу», иначе «Stevens» съезжает с центра
  const phone = useMedia('(max-width: 760px)');
  // Браузер не дал установить кнопкой — пункт всё равно есть и подсказывает, как установить через меню браузера
  const installItem = isInstalled()
    ? []
    : [{ label: T.menu_install, onSelect: () => (canInstall ? void install() : setHint(installHintKey())) }];

  // Ф10: «Уведомления: включить / выключить»; нет f10 на сервере или браузер не умеет — пункта нет
  const notifyOn = useSyncExternalStore(notifyStore.subscribe, () => notifyStore.get().on);
  const notifyItem = serverHas('f10') && typeof window !== 'undefined' && 'Notification' in window
    ? [{ label: notifyOn ? T.menu_notify_off : T.menu_notify_on,
         onSelect: () => void (notifyOn ? disableNotify() : enableNotify()) }]
    : [];

  const sandboxItems = [
    ...sandboxMenuExtra(T.menu_tour),
    ...notifyItem,
    { label: T.menu_reset_sandbox, onSelect: () => setDialog('reset') },
    ...installItem,
    ...(features.auth ? [{ label: T.menu_go_auth, onSelect: goAuth }] : []),
  ];

  const calendar = <HeaderRightStart />;
  const menu = (
    <>
      {mode === 'sandbox' && (
        <Menu
          items={sandboxItems}
          trigger={({ open, toggle }) => (
            <button type="button" className="sandbox-badge" aria-haspopup="menu" aria-expanded={open} onClick={toggle}>
              {T.header_sandbox}
              <ChevronDown className="sandbox-badge__chevron" strokeWidth={1.5} aria-hidden="true" />
            </button>
          )}
        />
      )}
      {mode === 'account' && features.auth && (
        <Menu
          heading={login ?? ''}
          items={[...notifyItem, ...installItem, { label: T.menu_logout, onSelect: logout }]}
          trigger={({ open, toggle }) => (
            <button type="button" className="avatar" aria-label={T.profile_label} aria-haspopup="menu" aria-expanded={open} onClick={toggle}>
              {(login ?? '?').charAt(0).toUpperCase()}
            </button>
          )}
        />
      )}
    </>
  );

  return (
    <header className="header">
      <div className="header__left">
        {!empty && (
          <button
            type="button"
            className="new-dialog"
            disabled={busy}
            aria-label={T.btn_new_dialog}
            onClick={() => { emitActivity('button-press'); setDialog('new'); }}
          >
            <Plus size={16} strokeWidth={1.5} aria-hidden="true" />
            <span className="new-dialog__text">{T.btn_new_dialog}</span>
          </button>
        )}
        {(titlebar || phone) && calendar}
        {titlebar && menu}
      </div>
      <div className="header__name">{T.app_name}</div>
      <div className="header__right">
        {!titlebar && !phone && calendar}
        {!titlebar && menu}
      </div>
      {dialog === 'new' && (
        <Modal
          title={T.new_dialog_title}
          text={T.new_dialog_text}
          confirm={T.new_dialog_confirm}
          cancel={T.new_dialog_cancel}
          onCancel={() => setDialog(null)}
          onConfirm={() => { setDialog(null); newDialog(); }}
        />
      )}
      {dialog === 'reset' && (
        <Modal
          title={T.reset_title}
          text={T.reset_text}
          confirm={T.reset_confirm}
          cancel={T.reset_cancel}
          onCancel={() => setDialog(null)}
          onConfirm={() => { setDialog(null); void resetSandbox(); }}
        />
      )}
    </header>
  );
}
