import { AuthScreen } from './components/AuthScreen';
import { Composer } from './components/Composer';
import { Feed } from './components/Feed';
import { Header } from './components/Header';
import { NotifyCard } from './components/NotifyCard';
import { StatusLine } from './components/StatusLine';
import { BottomTop, LayoutSidePanel } from './extensions/points';
import { useApp } from './state/store';

export function App() {
  const screen = useApp((s) => s.screen);
  if (screen === 'boot') return null;
  if (screen === 'auth') return <AuthScreen />;
  return (
    <div className="app">
      <Header />
      <div className="app__body">
        <main className="app__main">
          <NotifyCard />
          <Feed />
          <div className="bottom">
            <div className="bottom__column">
              <BottomTop />
              <StatusLine />
              <Composer />
            </div>
          </div>
        </main>
        <LayoutSidePanel />
      </div>
    </div>
  );
}
