import React from 'react';
import ReactDOM from 'react-dom/client';
import { Router, Route, Switch } from 'wouter';
import { TreadmillProvider } from './state/TreadmillContext';
import App from './App';
import Lobby from './routes/Lobby';
import Running from './routes/Running';
import Debug from './routes/Debug';
import './styles/global.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <TreadmillProvider>
      <Router>
        <App>
          <Switch>
            <Route path="/" component={Lobby} />
            <Route path="/run" component={Running} />
            <Route path="/debug" component={Debug} />
            <Route>
              <Lobby />
            </Route>
          </Switch>
        </App>
      </Router>
    </TreadmillProvider>
  </React.StrictMode>,
);
