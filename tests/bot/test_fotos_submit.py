from modules.photos.handlers import KIND, MODULE, submit


def ask(queue, args, uid=1):
    return submit(queue, args, chat_id=100 + uid, telegram_id=uid, user_name="Иван")


def test_enqueue_position(queue):
    assert ask(queue, "MH_1022") == "MH_1022: в очереди, позиция 1"
    assert ask(queue, "ko 2001") == "KO_2001: в очереди, позиция 2"
    job = queue.status().queued[0]
    assert (job.module, job.kind, job.key, job.chat_id, job.user_name) == \
        (MODULE, KIND, "MH_1022", 101, "Иван")


def test_bad_input_not_enqueued(queue):
    assert ask(queue, "1022").startswith("Укажи номер машины")
    assert ask(queue, "MH_1022 что-то") == 'Неизвестный вариант "что-то". Доступно: full'
    assert queue.status().queued == []


async def test_duplicate_queued_and_running(queue):
    answers = []

    async def handler(job):
        # пока задача выполняется, партнёр повторяет команду
        answers.append(ask(queue, "MH_1022", uid=2))

    queue.register_kind(KIND, handler)
    ask(queue, "MH_1022")
    ask(queue, "MH_1040")
    assert ask(queue, "mh1040", uid=2) == "MH_1040 уже в очереди, позиция 2"
    await queue.run_next()
    assert answers == ["MH_1022 уже обрабатывается"]
    assert [j.key for j in queue.status().queued] == ["MH_1040"]


def test_queue_full(queue):
    for n in range(10):
        ask(queue, f"MH_{1000 + n}")
    assert ask(queue, "MH_2000") == "Очередь переполнена (10), попробуй позже"


def test_duplicate_with_new_variant_says_not_added(queue):
    ask(queue, "MH_1040")
    ask(queue, "MH_1022")
    assert ask(queue, "mh1022 full", uid=2) == (
        "MH_1022 уже в очереди, позиция 2. Вариант full не добавлен — запроси его после завершения.")
    ask(queue, "KO_2001 full")
    assert ask(queue, "KO_2001 full", uid=2) == "KO_2001 уже в очереди, позиция 3"
    assert ask(queue, "KO_2001", uid=2) == "KO_2001 уже в очереди, позиция 3"


async def test_running_duplicate_with_new_variant(queue):
    answers = []

    async def handler(job):
        answers.append(ask(queue, "MH_1022 full", uid=2))

    queue.register_kind(KIND, handler)
    ask(queue, "MH_1022")
    await queue.run_next()
    assert answers == ["MH_1022 уже обрабатывается. Вариант full не добавлен — "
                       "запроси его после завершения."]
