import AppKit
import SwiftUI

// Alias avoids the SDK 27 State macro when building with Command Line Tools.
typealias ViewState<Value> = SwiftUI.State<Value>

private let accent = Color(red: 0.20, green: 0.50, blue: 0.93)

struct Panel<Content: View>: View {
    var title: String
    var icon: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label(title, systemImage: icon).font(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(22)
        .background(.background, in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(.primary.opacity(0.06)))
    }
}

struct RootView: View {
    @EnvironmentObject var model: AppModel
    let pages = [("overview", "Обзор", "slider.horizontal.3"),
                 ("rules", "Правила", "line.3.horizontal.decrease.circle"),
                 ("connection", "Подключение", "link"),
                 ("guide", "Как настроить", "questionmark.circle")]
    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) {
                    Image(systemName: "paperplane.fill").font(.system(size: 23)).foregroundStyle(accent)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Telegram").font(.system(size: 15, weight: .bold))
                        Text("FOCUS").font(.system(size: 11, weight: .semibold, design: .rounded)).tracking(3).foregroundStyle(.secondary)
                    }
                }.padding(.bottom, 30).padding(.leading, 8)
                ForEach(pages, id: \.0) { page in
                    Button {
                        model.selectedPage = page.0
                    } label: {
                        Label(page.1, systemImage: page.2)
                            .font(.system(size: 13, weight: model.selectedPage == page.0 ? .semibold : .regular))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 12).padding(.vertical, 12)
                            .foregroundStyle(model.selectedPage == page.0 ? accent : .primary)
                            .background(model.selectedPage == page.0 ? accent.opacity(0.11) : .clear,
                                        in: RoundedRectangle(cornerRadius: 10))
                    }.buttonStyle(.plain)
                }
                Spacer()
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 6) {
                        Circle().fill(model.statusColor).frame(width: 7, height: 7)
                        Text(model.connected ? (model.accountName.isEmpty ? "Telegram подключён" : model.accountName) : "Нет подключения")
                            .font(.system(size: 11, weight: .medium)).lineLimit(1)
                    }
                    Text("Правила работают на этом Mac")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                    Text("Версия \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—") · Apple Silicon")
                        .font(.system(size: 10)).foregroundStyle(.tertiary)
                }.padding(10)
            }
            .padding(.horizontal, 16).padding(.top, 34).padding(.bottom, 14)
            .frame(width: 200)
            .background(.regularMaterial)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    if !model.errorText.isEmpty {
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                            Text(model.errorText).font(.callout).textSelection(.enabled)
                            Spacer()
                            Button { model.errorText = "" } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
                        }.padding(14).background(.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 12))
                    }
                    switch model.selectedPage {
                    case "rules": RulesView()
                    case "connection": ConnectionView()
                    case "guide": GuideView()
                    default: OverviewView()
                    }
                }.padding(30).frame(maxWidth: 1000, alignment: .leading).frame(maxWidth: .infinity)
            }.background(Color(nsColor: .windowBackgroundColor).opacity(0.65))
        }
        .frame(minWidth: 960, minHeight: 680)
        .tint(accent)
        .onChange(of: model.selectedPage) { model.simulationAllowed = nil }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await model.refreshNotificationStatus() }
        }
        .confirmationDialog("Выйти из Telegram Focus?", isPresented: $model.showLogoutConfirmation, titleVisibility: .visible) {
            Button("Выйти и отозвать сессию", role: .destructive) { model.logout() }
            Button("Отмена", role: .cancel) {}
        } message: {
            Text("Обычный Telegram продолжит работать. Для этого приложения потребуется войти заново.")
        }
    }
}

struct PageTitle: View {
    let title: String
    let subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.system(size: 29, weight: .bold, design: .rounded))
            Text(subtitle).font(.system(size: 13)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct OverviewView: View {
    @EnvironmentObject var model: AppModel
    @ViewState<Bool> private var showFocusWarning = false
    var body: some View {
        PageTitle(title: "Только то, что важно.", subtitle: "Telegram остаётся рядом. Шум — за пределами внимания.")
        HStack(alignment: .top, spacing: 20) {
            VStack(alignment: .leading, spacing: 15) {
                Label(model.filterTitle.uppercased(),
                      systemImage: model.config.enabled ? "checkmark.shield.fill" : "pause.circle.fill")
                    .font(.system(size: 11, weight: .bold)).tracking(1.2)
                Text(!model.config.enabled ? "Можно выдохнуть." : (model.effectiveFilterEnabled ? "Личное. Срочное. Твоё." : (FocusAutomation.hasSigningTeam ? "Ждём фокусирование." : "Выбери ручной режим.")))
                    .font(.system(size: 24, weight: .semibold, design: .rounded))
                Text(model.effectiveFilterEnabled ? "Покажем сообщения, которые подходят под твои правила." : (model.config.enabled ? model.focusStatusText : "Telegram Focus сейчас не показывает уведомления."))
                    .font(.system(size: 13)).opacity(0.85)
                if !model.connected {
                    Button("Подключить Telegram →") { model.selectedPage = "connection" }
                        .buttonStyle(.bordered).tint(.white)
                } else {
                    HStack(spacing: 7) {
                        Circle().fill(.white).frame(width: 6, height: 6)
                        Text(model.statusText).font(.caption)
                    }
                }
            }
            Spacer(minLength: 0)
            Toggle("Фильтр", isOn: model.filterBinding).labelsHidden().toggleStyle(.switch)
                .controlSize(.large).tint(.white.opacity(0.4))
        }
        .padding(25).frame(maxWidth: .infinity, alignment: .leading)
        .foregroundStyle(.white)
        .background(LinearGradient(colors: model.effectiveFilterEnabled ? [accent, Color(red: 0.27, green: 0.34, blue: 0.75)] : [.gray, .gray.opacity(0.8)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: 20))

        HStack(spacing: 14) {
            StatTile(number: "\(model.passed)", label: "Пропущено", icon: "bell.badge", color: .green)
            StatTile(number: "\(model.suppressed)", label: "Заглушено", icon: "bell.slash", color: .secondary)
            StatTile(number: "\(model.config.people.count)", label: "Важных людей", icon: "person.crop.circle.badge.checkmark", color: accent)
        }
        Panel(title: "Фокусирование macOS", icon: "moon") {
            if FocusAutomation.hasSigningTeam {
                Toggle("Доставка только при фокусировании", isOn: Binding(
                    get: { model.config.onlyDuringFocus },
                    set: { enabled in
                        if enabled { model.setAutomaticMode(true) }
                        else { showFocusWarning = true }
                    }))
            } else {
                Text(FocusAutomation.unsupportedMessage).font(.callout)
                if model.config.onlyDuringFocus {
                    Button("Перейти в ручной режим") { showFocusWarning = true }
                        .buttonStyle(.borderedProminent)
                }
            }
            if model.config.onlyDuringFocus && FocusAutomation.hasSigningTeam {
                Text(model.focusStatusText).font(.callout)
                Text("Один раз в настройках нужного фокусирования добавь Telegram Focus в разрешённые приложения и в «Фильтры фокусирования». В фильтре включи «Включать фильтр TGFocus». Обычный Telegram оставь заглушённым. Вне настроенных режимов доставка TGFocus будет на паузе.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Открыть фокусирование macOS") { model.openSettings("com.apple.Focus-Settings.extension") }
                Button("Пошаговая инструкция →") { model.selectedPage = "guide" }.buttonStyle(.link)
            } else {
                Text("Ручной режим: если Telegram тоже показывает уведомления, возможны дубли. Выключай фильтр TGFocus, когда пользуешься обычными уведомлениями Telegram.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .alert("Возможны двойные уведомления", isPresented: $showFocusWarning) {
            Button("Перейти в ручной режим", role: .destructive) { model.setAutomaticMode(false) }
            Button("Отмена", role: .cancel) {}
        } message: {
            Text("TGFocus будет показывать уведомления и без фокусирования. Если обычный Telegram тоже показывает их, одно сообщение может прийти дважды. В ручном режиме выключай фильтр TGFocus, когда он не нужен.")
        }
        if !model.notificationsAllowed {
            Panel(title: "Разреши уведомления", icon: "bell") {
                HStack {
                    Text("macOS должна разрешить баннеры от Telegram Focus.").font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button("Разрешить") { model.requestNotifications() }.buttonStyle(.borderedProminent)
                }
            }
        }
        Panel(title: "Последние решения", icon: "clock.arrow.circlepath") {
            if model.activities.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "tray").font(.system(size: 28)).foregroundStyle(.tertiary)
                    Text("Здесь появятся решения по новым сообщениям")
                        .font(.callout).foregroundStyle(.secondary)
                    Text("Без текста переписки. Журнал очищается при выходе.")
                        .font(.caption).foregroundStyle(.tertiary)
                }.frame(maxWidth: .infinity).padding(.vertical, 20)
            } else {
                ForEach(model.activities.prefix(12)) { item in
                    HStack(spacing: 12) {
                        Image(systemName: item.allowed ? "bell.fill" : "bell.slash")
                            .foregroundStyle(item.allowed ? Color.green : .secondary).frame(width: 22)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(item.title).font(.system(size: 13, weight: .medium))
                            Text(item.reason).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(item.date, style: .time).font(.caption).foregroundStyle(.tertiary)
                        Button { model.openTelegram(item.url) } label: { Image(systemName: "arrow.up.right") }
                            .buttonStyle(.borderless).help("Открыть в Telegram")
                    }
                    if item.id != model.activities.prefix(12).last?.id { Divider() }
                }
                Button("Очистить журнал") { model.activities = []; model.passed = 0; model.suppressed = 0 }
                    .buttonStyle(.link).font(.caption)
            }
        }
        Text("Фильтр работает, пока приложение запущено, а Mac не спит. По умолчанию уведомления приостановлены вне фокусирования. Настрой разрешения во вкладке «Как настроить».")
            .font(.caption).foregroundStyle(.secondary)
    }
}

struct StatTile: View {
    let number: String
    let label: String
    let icon: String
    let color: Color
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon).font(.system(size: 20)).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 3) {
                Text(number).font(.system(size: 24, weight: .semibold, design: .rounded))
                Text(label).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }.padding(18).frame(maxWidth: .infinity)
            .background(.background, in: RoundedRectangle(cornerRadius: 15))
    }
}

struct RulesView: View {
    @EnvironmentObject var model: AppModel
    @ViewState<String> private var newKeyword = ""
    @ViewState<String?> private var picker: String?
    @ViewState<String> private var testText = "Можешь срочно позвонить?"
    @ViewState<String> private var testKind = "private"
    @ViewState<String> private var testChat = ""
    @ViewState<String> private var testPerson = ""
    @ViewState<Bool> private var testMentioned = false
    @ViewState<Bool> private var testReplied = false
    var body: some View {
        PageTitle(title: "Твои правила", subtitle: "Сохраняются автоматически. Применяются только к уведомлениям Telegram Focus.")
        Panel(title: "Личные сообщения", icon: "bubble.left.and.bubble.right") {
            Toggle("Пропускать все личные сообщения", isOn: $model.config.allPrivate)
                .onChange(of: model.config.allPrivate) { model.saveConfig() }
            Text(model.config.allPrivate ? "Любая личка проходит. Группы и каналы по-прежнему фильтруются." : "Проходят важные люди и сообщения с ключевыми словами. Остальные — тихо.")
                .font(.caption).foregroundStyle(.secondary)
            Divider()
            HStack {
                Text("Важные люди").font(.system(size: 13, weight: .medium))
                Spacer()
                Button("Добавить из Telegram", systemImage: "plus") { picker = "people"; model.loadDialogs() }
            }
            if model.config.people.isEmpty {
                Text("Пока никого. Добавленные люди проходят всегда, даже без текста: фото, голосовые, стикеры.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(model.config.people) { person in
                HStack {
                    Label(person.name, systemImage: "person.crop.circle")
                    Spacer()
                    Text("Всегда в личке").font(.caption).foregroundStyle(.secondary)
                    Button { model.config.people.removeAll { $0.id == person.id }; model.saveConfig() } label: { Image(systemName: "minus.circle") }
                        .buttonStyle(.borderless)
                }
            }
        }
        Panel(title: "Ключевые слова", icon: "text.magnifyingglass") {
            Text("Достаточно одного совпадения. Регистр и «ё/е» не важны; «срочно» не совпадёт с «несрочно». Формы слов добавляются отдельно.")
                .font(.caption).foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 145), alignment: .leading)], alignment: .leading, spacing: 8) {
                ForEach(model.config.keywords, id: \.self) { word in
                    HStack(spacing: 6) {
                        Text(word).font(.system(size: 12, weight: .medium)).lineLimit(1)
                        Spacer(minLength: 0)
                        Button { model.config.keywords.removeAll { $0 == word }; model.saveConfig() } label: { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)) }
                            .buttonStyle(.plain).foregroundStyle(.secondary)
                    }.padding(.horizontal, 10).padding(.vertical, 8).background(accent.opacity(0.09), in: Capsule())
                }
            }
            HStack {
                TextField("Новое слово или фраза", text: $newKeyword).textFieldStyle(.roundedBorder).onSubmit(addKeyword)
                Button("Добавить", action: addKeyword).disabled(newKeyword.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        Panel(title: "Группы и каналы", icon: "person.3") {
            HStack {
                Text("Обычные сообщения заглушены. Для нужных чатов добавь исключения.").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Исключение", systemImage: "plus") { picker = "chats"; model.loadDialogs() }
            }
            Toggle("Уведомлять об упоминаниях меня в группах", isOn: $model.config.allowMentions)
                .onChange(of: model.config.allowMentions) { model.saveConfig() }
            Toggle("Уведомлять об ответах на мои сообщения в группах", isOn: $model.config.allowReplies)
                .onChange(of: model.config.allowReplies) { model.saveConfig() }
            Text("Включённые обращения проходят без ключевых слов. При выключении сообщение всё ещё может пройти по другим правилам чата.")
                .font(.caption).foregroundStyle(.secondary)
            ForEach($model.config.chats) { $chat in
                VStack(spacing: 8) {
                    HStack {
                        Text(chat.name).font(.system(size: 13, weight: .medium))
                        Spacer()
                        Button { model.config.chats.removeAll { $0.id == chat.id }; model.saveConfig() } label: { Image(systemName: "minus.circle") }.buttonStyle(.borderless)
                    }
                    Toggle("Только с ключевыми словами", isOn: $chat.keywordsOnly)
                        .font(.caption).onChange(of: chat.keywordsOnly) { model.saveConfig() }
                    Divider()
                }
            }
        }
        Panel(title: "Проверить правило", icon: "play.circle") {
            Text("Проверка примера не учитывает фокусирование и mute-настройки реального чата.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Picker("Тип чата", selection: $testKind) {
                    Text("Личка").tag("private")
                    Text("Группа").tag("group")
                    Text("Канал").tag("channel")
                }.frame(width: 210)
                if testKind == "private" {
                    Picker("От кого", selection: $testPerson) {
                        Text("Обычный собеседник").tag("")
                        ForEach(model.config.people) { Text($0.name).tag($0.id) }
                    }
                } else {
                    Picker("Чат", selection: $testChat) {
                        Text("Обычный чат").tag("")
                        ForEach(model.config.chats) { Text($0.name).tag($0.id) }
                    }
                }
            }
            TextField("Текст сообщения", text: $testText, axis: .vertical).lineLimit(2...4).textFieldStyle(.roundedBorder)
            if testKind == "group" {
                Toggle("Упоминание меня", isOn: $testMentioned).font(.caption)
                Toggle("Ответ на моё сообщение", isOn: $testReplied).font(.caption)
            }
            HStack {
                Button("Проверить") { model.simulate(text: testText, kind: testKind, chatId: testChat, senderId: testPerson, mentioned: testKind == "group" && testMentioned, replyToMe: testKind == "group" && testReplied) }
                    .buttonStyle(.borderedProminent).disabled(!model.helperReady)
                if let allowed = model.simulationAllowed {
                    Label(allowed ? "Пропустим" : "Заглушим", systemImage: allowed ? "checkmark.circle.fill" : "bell.slash.fill")
                        .foregroundStyle(allowed ? Color.green : .secondary)
                    Text(model.simulationReason).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .onChange(of: testText) { model.simulationAllowed = nil }
        .onChange(of: testKind) { model.simulationAllowed = nil }
        .onChange(of: testChat) { model.simulationAllowed = nil }
        .onChange(of: testPerson) { model.simulationAllowed = nil }
        .onChange(of: testMentioned) { model.simulationAllowed = nil }
        .onChange(of: testReplied) { model.simulationAllowed = nil }
        Panel(title: "Внешний вид уведомлений", icon: "bell.badge") {
            Toggle("Показывать текст сообщения", isOn: $model.config.showPreview)
                .onChange(of: model.config.showPreview) { model.saveConfig() }
            Toggle("Звук уведомления", isOn: $model.config.playSound)
                .onChange(of: model.config.playSound) { model.saveConfig() }
        }
        .sheet(item: Binding(get: { picker.map { PickerMode(id: $0) } }, set: { picker = $0?.id })) { mode in
            DialogPicker(mode: mode.id)
        }
    }
    func addKeyword() {
        let word = newKeyword.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !word.isEmpty else { return }
        if !model.config.keywords.contains(word) { model.config.keywords.append(word); model.saveConfig() }
        newKeyword = ""
    }
}

struct PickerMode: Identifiable { var id: String }

struct DialogPicker: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let mode: String
    @ViewState<String> private var search = ""
    var choices: [Dialog] {
        model.dialogs.filter { (mode == "people" ? $0.kind == "private" : $0.kind != "private") &&
            (search.isEmpty || $0.name.localizedCaseInsensitiveContains(search)) }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(mode == "people" ? "Важный собеседник" : "Исключение для чата").font(.title2.bold())
                Spacer()
                Button("Готово") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            TextField("Поиск по последним 500 диалогам", text: $search).textFieldStyle(.roundedBorder)
            if model.loadingDialogs {
                ProgressView("Загружаю диалоги…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if !model.connected {
                Text("Сначала подключи Telegram во вкладке «Подключение».")
                    .foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(choices) { dialog in
                    let added = mode == "people" ? model.config.people.contains { $0.id == dialog.id } : model.config.chats.contains { $0.id == dialog.id }
                    HStack {
                        Image(systemName: dialog.kind == "private" ? "person.crop.circle" : dialog.kind == "group" ? "person.3" : "megaphone")
                            .foregroundStyle(accent).frame(width: 28)
                        Text(dialog.name)
                        Spacer()
                        Button(added ? "Добавлен" : "Добавить") {
                            if mode == "people" { model.config.people.append(Person(id: dialog.id, name: dialog.name)) }
                            else { model.config.chats.append(ChatRule(id: dialog.id, name: dialog.name)) }
                            model.saveConfig()
                        }.disabled(added)
                    }.padding(.vertical, 4)
                }.listStyle(.inset)
                if choices.isEmpty { Text("Подходящих диалогов нет.").foregroundStyle(.secondary) }
            }
            if model.connected { Button("Обновить список") { model.loadDialogs() }.disabled(model.loadingDialogs) }
        }.padding(24).frame(width: 560, height: 450)
    }
}

struct ConnectionView: View {
    @EnvironmentObject var model: AppModel
    @ViewState<String> private var phone = ""
    @ViewState<String> private var code = ""
    @ViewState<String> private var password = ""
    var body: some View {
        PageTitle(title: "Подключение", subtitle: "Одна дополнительная сессия Telegram. Читаем новые сообщения, чтобы проверить правила.")
        Panel(title: "Аккаунт Telegram", icon: "paperplane") {
            HStack {
                Circle().fill(model.statusColor).frame(width: 8, height: 8)
                Text(model.statusText).font(.callout)
                if model.busy { ProgressView().controlSize(.small) }
                Spacer()
            }
            if model.connected {
                Text(model.accountName).font(.title3.bold())
                Text("Вход сохранён в Связке ключей. При следующем запуске приложение подключится автоматически.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Обновить диалоги") { model.loadDialogs() }.disabled(model.loadingDialogs)
                    Spacer()
                    Button("Выйти из аккаунта", role: .destructive) { model.showLogoutConfirmation = true }.disabled(model.busy)
                }
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    Text("1. Получи ключи приложения").font(.system(size: 14, weight: .semibold))
                    Text("Открой my.telegram.org → API development tools. Создай приложение: название Personal Focus, короткое имя — своё уникальное латинское. Не используй слово Telegram в названии. Платформа Desktop. Скопируй API ID и API Hash сюда.")
                        .font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                    Link("Открыть my.telegram.org ↗", destination: URL(string: "https://my.telegram.org/apps")!)
                }
                HStack(alignment: .top, spacing: 14) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("API ID").font(.caption).foregroundStyle(.secondary)
                        TextField("Например, 12345678", text: $model.credentials.apiId).textFieldStyle(.roundedBorder)
                    }.frame(width: 190)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("API Hash").font(.caption).foregroundStyle(.secondary)
                        SecureField("32 символа", text: $model.credentials.apiHash).textFieldStyle(.roundedBorder)
                    }
                }.disabled(model.busy || model.authStep)
                if model.authStep {
                    Divider()
                    Text("2. Войди в Telegram").font(.system(size: 14, weight: .semibold))
                    switch model.state {
                    case "phone":
                        Button { model.authAction("qr") } label: {
                            Label("Войти по QR-коду — без ожидания сообщения", systemImage: "qrcode")
                        }.buttonStyle(.borderedProminent).disabled(model.busy)
                        Text("Или используй номер телефона:").font(.caption).foregroundStyle(.secondary)
                        HStack {
                            TextField("Номер телефона: +7…", text: $phone).textFieldStyle(.roundedBorder)
                                .onSubmit { model.auth(command: "phone", value: phone) }
                            Button("Получить код") { model.auth(command: "phone", value: phone) }
                                .buttonStyle(.borderedProminent).disabled(model.busy || phone.isEmpty)
                        }
                    case "code":
                        Label("Способ доставки: \(model.deliveryMethod)", systemImage: "envelope")
                            .font(.callout).foregroundStyle(.secondary)
                        HStack {
                            SecureField("Код входа", text: $code).textFieldStyle(.roundedBorder)
                                .onSubmit { model.auth(command: "code", value: code); code = "" }
                            Button("Войти") { model.auth(command: "code", value: code); code = "" }
                                .buttonStyle(.borderedProminent).disabled(model.busy || code.isEmpty)
                        }
                        HStack {
                            Button("Код не пришёл — войти по QR") { code = ""; model.authAction("qr") }
                                .disabled(model.busy)
                            if model.canResend {
                                TimelineView(.periodic(from: .now, by: 1)) { context in
                                    let remaining = max(0, Int(ceil(model.resendAt.timeIntervalSince(context.date))))
                                    Button(remaining > 0 ? "Повторить через \(remaining) сек." : "Повторить доставку") { model.authAction("resend") }
                                        .disabled(model.busy || remaining > 0)
                                }
                            }
                        }
                    case "qr":
                        HStack(alignment: .center, spacing: 24) {
                            if let image = model.qrImage {
                                Image(nsImage: image).interpolation(.none).resizable().scaledToFit()
                                    .frame(width: 200, height: 200).padding(18).background(.white, in: RoundedRectangle(cornerRadius: 14))
                                    .accessibilityLabel("QR-код для входа в Telegram Focus")
                            } else {
                                ProgressView("Готовлю QR-код…").frame(width: 236, height: 236)
                            }
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Открой Telegram на телефоне").font(.headline)
                                Text("Настройки → Устройства → Подключить устройство. Наведи камеру на этот QR-код и подтверди вход.")
                                    .font(.callout).foregroundStyle(.secondary)
                                TimelineView(.periodic(from: .now, by: 1)) { context in
                                    Text("QR действует ещё \(max(0, Int(ceil(model.qrExpiresAt.timeIntervalSince(context.date))))) сек.")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    case "qrExpired":
                        Button("Обновить QR", systemImage: "arrow.clockwise") { model.authAction("qr") }
                            .buttonStyle(.borderedProminent).disabled(model.busy)
                    case "authUnsupported":
                        Button("Войти по QR-коду", systemImage: "qrcode") { model.authAction("qr") }
                            .buttonStyle(.borderedProminent).disabled(model.busy)
                    case "password":
                        HStack {
                            SecureField("Пароль двухэтапной аутентификации", text: $password).textFieldStyle(.roundedBorder)
                                .onSubmit { model.auth(command: "password", value: password); password = "" }
                            Button("Продолжить") { model.auth(command: "password", value: password); password = "" }
                                .buttonStyle(.borderedProminent).disabled(model.busy || password.isEmpty)
                        }
                    default: EmptyView()
                    }
                    Button("Выбрать другой способ входа") { code = ""; password = ""; model.authAction("cancelAuth") }
                        .buttonStyle(.link).disabled(model.busy)
                } else {
                    Button(model.credentials.session.isEmpty ? "Подключить" : "Переподключить") { model.connect() }
                        .buttonStyle(.borderedProminent).disabled(model.busy || !model.helperReady)
                }
            }
            Text("Пароль и код не сохраняются. API-ключи и сессия хранятся в Связке ключей этого Mac.")
                .font(.caption).foregroundStyle(.secondary)
        }
        Panel(title: "Уведомления macOS", icon: "bell") {
            HStack {
                Label(model.notificationStatus, systemImage: model.notificationsAllowed ? "checkmark.circle.fill" : "exclamationmark.circle")
                    .foregroundStyle(model.notificationsAllowed ? Color.green : .orange)
                Spacer()
                Button("Тестовое уведомление") { model.requestNotifications(test: true) }
            }
            Button("Открыть настройки уведомлений") { model.openSettings("com.apple.Notifications-Settings.extension") }.buttonStyle(.link)
        }
        Panel(title: "Модуль Telegram", icon: "network") {
            HStack {
                Label(model.helperReady ? "Работает" : "Остановлен", systemImage: model.helperReady ? "checkmark.circle" : "xmark.circle")
                Spacer()
                if !model.helperReady {
                    Button("Перезапустить модуль") { model.startHelper() }
                }
            }
            Text("Соединение идёт напрямую к Telegram. Фильтрация локальная, без внешней нейросети. Приложение не отправляет сообщения и не отмечает их прочитанными.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct GuideView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        PageTitle(title: "Первая настройка", subtitle: "Пройди шаги по порядку. Python, терминал и автоматизации в «Командах» не понадобятся.")
        Panel(title: "1. Установи и открой приложение", icon: "square.and.arrow.down") {
            Text("Открой скачанный DMG, перетащи Telegram Focus в Applications (Программы), извлеки образ диска и запусти приложение из Программ.")
                .font(.callout).foregroundStyle(.secondary)
            Text("Если macOS блокирует первый запуск, открой Системные настройки → Конфиденциальность и безопасность → «Всё равно открыть». Разрешай запуск только для сборки из репозитория MXLev/tgfocus; отключать защиту macOS не нужно.")
                .font(.callout).foregroundStyle(.secondary)
            Text("Найди самолётик в строке меню → «Открыть настройки…». Значка в Dock нет — это нормально.")
                .font(.callout).foregroundStyle(.secondary)
        }
        Panel(title: "2. Подключи свой Telegram", icon: "link") {
            Text("Открой my.telegram.org/apps, войди в свой аккаунт Telegram и получи API ID и API Hash в разделе API development tools. Если ключи уже есть, используй их. Для регистрации можно выбрать название Personal Focus, уникальное короткое имя латиницей и платформу Desktop.")
                .font(.callout).foregroundStyle(.secondary)
            Link("Получить API ID и API Hash", destination: URL(string: "https://my.telegram.org/apps")!)
            Text("Во вкладке «Подключение» введи оба значения и нажми «Подключить», затем «Войти по QR-коду». На телефоне: Telegram → Настройки → Устройства → Подключить устройство. Отсканируй QR и подтверди вход. Если Telegram попросит пароль двухэтапной аутентификации, введи его в приложении. Можно также войти по номеру и коду.")
                .font(.callout).foregroundStyle(.secondary)
            Text("Ключи, код входа, пароль и сессию никому не отправляй. После подключения в приложении появится статус аккаунта.")
                .font(.caption).foregroundStyle(.secondary)
            Button("К подключению →") { model.selectedPage = "connection" }.buttonStyle(.link)
        }
        Panel(title: "3. Выбери, что для тебя важно", icon: "line.3.horizontal.decrease.circle") {
            Text("В «Правилах» добавь важных людей и ключевые слова. От важных людей проходят все личные сообщения; от остальных — сообщения с нужными словами, если ты не разрешил все личные.")
                .font(.callout).foregroundStyle(.secondary)
            Text("Для групп и каналов добавь нужные исключения. Уведомления об упоминаниях и ответах на твои сообщения включаются отдельно. Чаты и темы форумов, заглушённые в Telegram, останутся тихими даже при совпадении ключевого слова.")
                .font(.callout).foregroundStyle(.secondary)
            Text("Кнопка «Проверить» позволяет проверить пример без отправки сообщения. Она проверяет правила, но не системные разрешения и настройки реального чата.")
                .font(.caption).foregroundStyle(.secondary)
            Button("К правилам →") { model.selectedPage = "rules" }.buttonStyle(.link)
        }
        Panel(title: FocusAutomation.hasSigningTeam ? "4. Разреши уведомления и включи авто-режим" : "4. Разреши уведомления и выбери ручной режим", icon: "bell.badge") {
            Text("Нажми «Разрешить уведомления» и подтверди системный запрос. Затем открой Системные настройки → Уведомления → Telegram Focus: включи допуск уведомлений и баннеры.")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("Разрешить уведомления") { model.requestNotifications() }.buttonStyle(.borderedProminent)
                Button("Открыть уведомления macOS") { model.openSettings("com.apple.Notifications-Settings.extension") }
            }
            Text(FocusAutomation.hasSigningTeam ? "В «Обзоре» включи «Доставка только при фокусировании» и главный переключатель фильтра. Если фокусирование пока не настроено, приложение покажет ожидание." : "Бесплатная сборка с ad-hoc подписью не поддерживает системные фильтры фокусирования: macOS требует Team ID в подписи. В «Обзоре» нажми «Перейти в ручной режим» и подтверди предупреждение о дублях. Если ручной режим уже выбран, оставь его включённым. До подтверждения сохранённый авто-режим держит доставку на паузе.")
                .font(.callout).foregroundStyle(.secondary)
            Button("К обзору →") { model.selectedPage = "overview" }.buttonStyle(.link)
        }
        Panel(title: "5. Настрой режим фокусирования macOS", icon: "moon") {
            Text("Открой Системные настройки → Фокусирование → «Не беспокоить» или создай свой режим. В разделе «Приложения» выбери «Разрешить уведомления от» и добавь Telegram Focus. Обычный Telegram оставь вне списка разрешённых приложений.")
                .font(.callout).foregroundStyle(.secondary)
            Text("В настройках уведомлений обычного Telegram отключи неотложные уведомления, если такой переключатель доступен, чтобы он не обходил фильтр.")
                .font(.callout).foregroundStyle(.secondary)
            Text(FocusAutomation.hasSigningTeam ? "Прокрути страницу выбранного режима до раздела «Фильтры фокусирования» → «Добавить фильтр» → Telegram Focus → «Важные сообщения Telegram». Включи «Включать фильтр TGFocus» и нажми «Добавить» или «Готово». Повтори для каждого нужного режима." : "В ручном режиме не добавляй TGFocus в раздел «Фильтры фокусирования»: кнопка Add в ad-hoc сборке может быть неактивна. Достаточно разрешить TGFocus в разделе «Приложения».")
                .font(.caption).foregroundStyle(.secondary)
            Text(FocusAutomation.hasSigningTeam ? "Для авто-режима TGFocus нужен и в разрешённых приложениях, и в фильтрах фокусирования. Team ID в подписи — только предварительное условие: создание фильтра и переключение режима нужно проверить на этом Mac. Если фильтр не удаётся добавить, используй ручной режим." : "Включай главный фильтр TGFocus вручную вместе с фокусированием macOS. Перед выключением фокусирования выключай фильтр TGFocus — иначе обычный Telegram и TGFocus могут показать одно сообщение дважды.")
                .font(.caption).foregroundStyle(.secondary)
            Button("Открыть фокусирование") { model.openSettings("com.apple.Focus-Settings.extension") }.buttonStyle(.borderedProminent)
        }
        Panel(title: "6. Проверь, что всё работает", icon: "checkmark.shield") {
            Text("Включи настроенный режим через Пункт управления → Фокусирование. В «Обзоре» включи фильтр TGFocus, затем нажми «Тестовое уведомление»: должен появиться баннер TGFocus.")
                .font(.callout).foregroundStyle(.secondary)
            Button("Тестовое уведомление") { model.requestNotifications(test: true) }
            Text("Попроси другого человека написать «срочно» в незаглушённую личку. Проверь реальный баннер, а не только запись в журнале. Сообщение, не подходящее под твои правила, и сообщение из заглушённого чата должны остаться тихими.")
                .font(.callout).foregroundStyle(.secondary)
            Text(FocusAutomation.hasSigningTeam && model.config.onlyDuringFocus ? "Оставь фильтр TGFocus включённым и выключи фокусирование. TGFocus должен молчать; обычный Telegram сможет показать своё уведомление. Снова включи фокусирование и проверь возобновление доставки. Главный выключатель должен останавливать её даже во время фокусирования." : "Выключи главный фильтр TGFocus, затем выключи фокусирование macOS. Попроси отправить ещё одно сообщение: TGFocus должен молчать, обычный Telegram сможет показать своё уведомление. Снова включи фокусирование и главный фильтр, чтобы вернуть доставку TGFocus.")
                .font(.callout).foregroundStyle(.secondary)
            Text("Если баннера нет, проверь подключение Telegram, разрешение уведомлений macOS, список разрешённых приложений выбранного режима и mute самого чата.")
                .font(.caption).foregroundStyle(.secondary)
        }
        Panel(title: "Готово — как пользоваться дальше", icon: "sun.max") {
            Text(FocusAutomation.hasSigningTeam && model.config.onlyDuringFocus ? "В авто-режиме оставь главный фильтр включённым. Главный выключатель в меню и в настройках всегда останавливает доставку." : "В ручном режиме включай и выключай фильтр самостоятельно. Главный выключатель в меню и в настройках всегда останавливает доставку. Вне фокусирования возможны дубли с Telegram, если оставить фильтр включённым.")
                .font(.callout).foregroundStyle(.secondary)
            Divider()
            Text("Закрытие окна настроек оставляет фильтр работающим. Чтобы полностью остановить приложение, нажми самолётик → «Завершить Telegram Focus». Во сне, при выключенном Mac или без интернета новые сообщения не обрабатываются.")
                .font(.caption).foregroundStyle(.secondary)
            Text("Ключевые слова проверяются в тексте и подписях к медиа. Голосовые не расшифровываются, звонки и секретные чаты не поддерживаются.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
