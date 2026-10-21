import AppKit
import SwiftUI

struct AutomationSettingsView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        Panel(title: "Управление фильтром", icon: "hand.tap") {
            Toggle("Автоматически по Focus через «Команды»", isOn: $model.config.shortcutAutomation)
                .onChange(of: model.config.shortcutAutomation) { model.saveConfig() }
            Text(model.automationStatus).font(.callout)
            if model.config.shortcutAutomation {
                TextField("Название команды", text: $model.config.shortcutName)
                    .textFieldStyle(.roundedBorder).onSubmit { model.saveConfig() }
                Picker("Проверять каждые", selection: $model.config.shortcutInterval) {
                    Text("15 секунд").tag(15)
                    Text("30 секунд").tag(30)
                    Text("60 секунд").tag(60)
                }.onChange(of: model.config.shortcutInterval) { model.saveConfig() }
                HStack {
                    Button("Проверить сейчас") { model.saveConfig(); model.checkShortcut() }
                        .disabled(!model.config.enabled)
                    if let date = model.shortcutLastCheck {
                        Text("Последняя проверка: \(date.formatted(date: .omitted, time: .standard))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text("Один раз создай команду по инструкции ниже. Включи главный переключатель фильтра: после этого Focus управляет доставкой. Неизвестное состояние или ошибка команды приостанавливают доставку. При выключенном главном фильтре команда не запускается.")
                    .font(.caption).foregroundStyle(.secondary)
                Text("Переключение замечается при следующей проверке. До неё после выхода из Focus возможны дубли уведомлений с Telegram. Выполнение команды может занять до 10 секунд.")
                    .font(.caption).foregroundStyle(.orange)
                ShortcutGuideContent()
            } else {
                Text("Включай и выключай фильтр выше или через самолётик в строке меню. Перед выходом из Focus выключи его, чтобы избежать дублей с обычным Telegram.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Button("Как настроить →") { model.selectedPage = "guide" }.buttonStyle(.link)
        }.onDisappear { model.saveConfig() }
    }
}

struct ShortcutGuideContent: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Разовая настройка в «Командах»").font(.headline)
            Text("1. Открой приложение Apple «Команды» и создай новую команду с именем TGFocus State. Если выберешь другое имя, укажи его в настройке TGFocus.")
            Text("2. Добавь «Получить текущий режим фокусирования» (Get Current Focus). Затем «Если»: условие — у полученного режима есть значение (has any value).")
            Text("3. В ветке «Если» добавь действие «Текст» со значением ON. В ветке «Иначе» — действие «Текст» со значением OFF. Используй заглавные латинские буквы без кавычек.")
            Text("4. После «Конец если» добавь «Остановить и вывести результат» (Stop and Output). В качестве результата выбери переменную «Результат Если» (If Result), а не сам режим фокусирования.")
            Text("5. Запусти команду вручную и разреши запрошенный доступ. Проверь: при включённом Focus результат ON, при выключенном OFF. Никаких действий «Установить фокусирование» в команде быть не должно.")
            Text("6. В TGFocus включи «Автоматически по Focus через Команды» и главный переключатель. Нажми «Проверить сейчас». Проверь оба состояния, в том числе запуск приложения при уже включённом Focus.")
            Button("Открыть «Команды»") {
                NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/Shortcuts.app"))
            }
            Text("Это обычная пользовательская команда, а не системный Focus Filter. Подписка Apple Developer, Xcode и полный доступ к диску не нужны. Для доставки во время Focus всё равно разреши Telegram Focus в списке приложений выбранного режима.")
        }.font(.caption).foregroundStyle(.secondary)
    }
}

struct AISettingsView: View {
    @EnvironmentObject var model: AppModel
    @ViewState<String> private var key = ""
    @ViewState<Bool> private var showExclusions = false
    var body: some View {
        PageTitle(title: "ИИ-фильтр", subtitle: "Необязательная оценка важности вместо ключевых слов. По умолчанию выключена.")
        Panel(title: "Отправка сообщений провайдеру", icon: "sparkles") {
            Text("При включении текст подходящих сообщений будет передаваться OpenRouter и выбранному им провайдеру модели либо напрямую DeepSeek. Это внешние сервисы со своими правилами хранения данных. Не включай для конфиденциальной переписки; заранее добавь исключения ниже.")
                .font(.callout).foregroundStyle(.secondary)
            Toggle("Отправлять сообщения в ИИ для оценки важности", isOn: $model.config.aiEnabled)
                .onChange(of: model.config.aiEnabled) {
                    model.aiStatus = model.config.aiEnabled ? "Ожидаю проверки или подходящего сообщения" : "ИИ выключен"
                    model.saveConfig()
                }
            Text("Отправляются только первые 4000 символов текста или подписи. Имена, ID чатов, история и вложения не добавляются. Сам текст может содержать личные данные. Уже отправленный запрос отменой настройки отозвать нельзя.")
                .font(.caption).foregroundStyle(.secondary)
        }
        Panel(title: "Провайдер и ключ", icon: "key") {
            Picker("Провайдер", selection: $model.config.aiProvider) {
                Text("OpenRouter").tag("openrouter")
                Text("DeepSeek").tag("deepseek")
            }.onChange(of: model.config.aiProvider) {
                key = ""
                model.config.aiModel = model.config.aiProvider == "openrouter" ? "openrouter/free" : "deepseek-chat"
                model.aiStatus = "Выбран другой провайдер"
                model.saveConfig()
            }
            TextField("ID модели", text: $model.config.aiModel).textFieldStyle(.roundedBorder)
                .onSubmit { model.saveConfig() }
            Text(model.config.aiProvider == "openrouter" ?
                 "openrouter/free выбирает доступную бесплатную модель. Доступность, лимиты и требования к аккаунту определяет OpenRouter. Можно указать другой ID; платные модели расходуют баланс. Запрашиваем маршрутизацию без сбора данных; если подходящего провайдера нет, используются локальные правила." :
                 "deepseek-chat использует API DeepSeek. Бесплатность веб-чата не означает бесплатный API; проверь тариф и баланс.")
                .font(.caption).foregroundStyle(.secondary)
            Link("Получить API-ключ", destination: URL(string: model.config.aiProvider == "openrouter" ?
                "https://openrouter.ai/keys" : "https://platform.deepseek.com/api_keys")!)
            SecureField("Новый API-ключ", text: $key).textFieldStyle(.roundedBorder)
            HStack {
                Button("Сохранить ключ") { model.saveAIKey(key); key = "" }
                    .disabled(key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Text(model.aiKeyProviders.contains(model.config.aiProvider) ? "Ключ сохранён в Связке ключей" : "Ключ не задан")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Удалить ключ", role: .destructive) { model.saveAIKey(""); key = "" }
                    .disabled(!model.aiKeyProviders.contains(model.config.aiProvider))
            }
            Button("Проверить на тестовом тексте") {
                model.saveConfig()
                model.aiStatus = "Проверяю на вымышленном сообщении…"
                model.send(["command": "testAI"])
            }.disabled(!model.helperReady || !model.config.aiEnabled || !model.aiKeyProviders.contains(model.config.aiProvider))
            Text(model.aiStatus).font(.caption).foregroundStyle(.secondary)
        }
        Panel(title: "Что считать важным", icon: "slider.horizontal.3") {
            Picker("Уведомлять", selection: $model.config.aiMinimumPriority) {
                Text("Обычный, высокий и срочный приоритет").tag("normal")
                Text("Высокий и срочный приоритет").tag("high")
                Text("Только срочный приоритет").tag("urgent")
            }.onChange(of: model.config.aiMinimumPriority) { model.saveConfig() }
            Text("Дополнительный контекст: что важно именно тебе (до 2000 символов)").font(.caption)
            TextEditor(text: $model.config.aiInstruction).frame(height: 80)
                .onChange(of: model.config.aiInstruction) {
                    model.config.aiInstruction = String(model.config.aiInstruction.prefix(2000))
                    model.saveConfig()
                }
            Stepper("Не больше \(model.config.aiHourlyLimit) запросов в час", value: $model.config.aiHourlyLimit, in: 1...300)
                .onChange(of: model.config.aiHourlyLimit) { model.saveConfig() }
            Text("ИИ заменяет слова в обычной личке и в добавленных исключениях для групп/каналов с проверкой важности. Важные люди, все разрешённые личные, включённые упоминания и ответы проходят локально. Остальные группы и каналы в ИИ не отправляются.")
                .font(.caption).foregroundStyle(.secondary)
            Text("При ошибке, лимите, отсутствии ключа или неподдерживаемом ответе применяются обычные правила со словами. Не больше двух запросов одновременно, без очереди. ИИ может ошибаться и задерживает уведомления; не полагайся на него для экстренной связи.")
                .font(.caption).foregroundStyle(.secondary)
        }
        Panel(title: "Никогда не отправлять в ИИ", icon: "hand.raised") {
            Text("Сообщения этих диалогов проверяются только локальными правилами. Исключение действует и при проверке важности; заглушённые в Telegram чаты и темы также не отправляются в ИИ.")
                .font(.callout).foregroundStyle(.secondary)
            Button("Добавить чат или собеседника", systemImage: "plus") {
                showExclusions = true
                model.loadDialogs()
            }
            ForEach(model.config.aiExcludedChats) { chat in
                HStack {
                    Text(chat.name)
                    Spacer()
                    Button {
                        model.config.aiExcludedChats.removeAll { $0.id == chat.id }
                        model.saveConfig()
                    } label: { Image(systemName: "minus.circle") }.buttonStyle(.borderless)
                }
            }
        }
        .sheet(isPresented: $showExclusions) { DialogPicker(mode: "aiExcluded") }
        .onDisappear { key = ""; model.saveConfig() }
    }
}
