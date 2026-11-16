"""Толчок вместо опроса: поток задания будит страницу.

`service.JobService` про NiceGUI не знает и знать не должен, поэтому здесь два
слоя: потокобезопасный список подписчиков (`Hub`) и будильник на события
задания (`watch`). Будильник не правит сервис: он подменяет список событий
задания **до** старта потока, поэтому ни один шаг не теряется и ни одна строка
`webapp/service.py` не меняется.

`rev` — номер события задания (`seq`): он монотонен, и страница по разрыву
понимает, что пропустила шаг, и берёт полный снапшот.
"""
import threading


class Hub:
    """Подписчики на шаги задания. Публикуется из потока задания."""

    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def subscribe(self, callback):
        with self._lock:
            if callback not in self._subs:
                self._subs.append(callback)

    def unsubscribe(self, callback):
        with self._lock:
            self._subs = [item for item in self._subs if item != callback]

    def subscribers(self):
        with self._lock:
            return len(self._subs)

    def publish(self, rev, job_id):
        """Один шаг задания. Ошибка подписчика не должна ронять задание."""
        with self._lock:
            subs = list(self._subs)
        for callback in subs:
            try:
                callback(rev, job_id)
            except Exception:
                pass


hub = Hub()


class _Events(list):
    """Список событий задания, который заодно будит подписчиков.

    Задание само пишет в `job.events` (`JobService._run`), здесь только добавка
    после каждого шага. Список подменяется в `_Pool.submit`, то есть в том
    потоке, который ставит задание, — раньше, чем поток задания начнёт писать.
    """

    def __init__(self, job_id):
        super().__init__()
        self.job_id = str(job_id or "")

    def append(self, event):
        super().append(event)
        hub.publish(int(event.get("seq") or 0), self.job_id)


class _Pool:
    """Обёртка пула заданий: подменяет список событий до старта потока."""

    def __init__(self, inner):
        self._inner = inner

    def submit(self, fn, *args, **kwargs):
        job = args[0] if args else None
        events = getattr(job, "events", None)
        if isinstance(events, list) and not isinstance(events, _Events):
            job.events = _Events(getattr(job, "id", ""))
        return self._inner.submit(fn, *args, **kwargs)

    def shutdown(self, *args, **kwargs):
        return self._inner.shutdown(*args, **kwargs)


def watch(service):
    """Вешает будильник на задания сервиса. Повторный вызов ничего не меняет."""
    pool = getattr(service, "_pool", None)
    if pool is None or isinstance(pool, _Pool):
        return service
    service._pool = _Pool(pool)
    return service
