// Minimal typings for the few chrome.* APIs used, so typescript stays the only devDependency.
declare namespace chrome {
  namespace tabs {
    interface Tab {
      active: boolean;
      url?: string;
      title?: string;
      audible?: boolean;
      incognito: boolean;
    }
    interface ChangeInfo {
      status?: string;
      title?: string;
      audible?: boolean;
    }
    interface Event<F> {
      addListener(cb: F): void;
    }
    const onActivated: Event<() => void>;
    const onUpdated: Event<(tabId: number, info: ChangeInfo, tab: Tab) => void>;
    function query(q: { active: boolean; lastFocusedWindow: boolean }): Promise<Tab[]>;
  }
  namespace windows {
    const onFocusChanged: tabs.Event<() => void>;
  }
  namespace alarms {
    const onAlarm: tabs.Event<() => void>;
    function create(name: string, info: { periodInMinutes: number }): void;
  }
  namespace storage {
    const sync: { get(key: string): Promise<Record<string, unknown>> };
  }
}
