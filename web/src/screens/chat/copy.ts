// Settings → Chat's words (spec 5.2, 5.4, 9.2, 9.3). Server lines (the disclosure, the gateway, spending) are shown as sent.
export const CHAT = 'Chat';
export const LEDE = 'Answers come from Claude. Steward replaces the account numbers and names it holds, except store and payee names a manager approves. Your gateway looks for other names and can miss one.';
export const CHAT_IN = 'Chat in {book}';
export const ON = 'On';
export const OFF = 'Off';
export const VIEWER_SWITCH = 'Only a manager of this book can turn chat on or off.';
export const TURN_ON_TITLE = 'Turn on chat';
export const TURN_ON = 'Turn on';
export const NOT_NOW = 'Not now';
export const WHATS_SENT = "What's sent";
export const SPENDING = 'Spending';
export const NO_BUDGET = 'Spending shows here when {gateway} answers.';
export const BUDGET_LABEL = 'Monthly budget, in dollars';
export const BUDGET_HELP = 'From $1 to $100. Chat stops for the month when it is used up.';
export const MODEL_LABEL = 'Answers from';
export const SAVE = 'Save';
export const SAVED = 'Saved.';
export const BY_MONTH = 'By month';
export const BY_BOOK = 'This month by book';
export const BY_PERSON = 'This month by person';
export const ONE_QUESTION = '1 question';
export const N_QUESTIONS = '{n} questions';
export const SENT = 'Everything sent so far';
export const SENT_LEDE = "Everything sent from this book, newest first. Every member can read it, and it can't be changed or deleted.";
export const NOTHING_SENT = 'Nothing has been sent from this book.';
export const SHOW_MORE = 'Show more';
export const DOWNLOAD = 'Download as a JSON file';

// The Ask page (plan B; docs/design/chat). Server lines are shown as sent.
export const ASK_TITLE = 'Ask about {book}';
export const HISTORY = 'History';
export const NEW = 'New';
export const BACK_TO_QUESTION = 'Back to the question';
export const VIEWER_TAG = "You're a viewer in this book. You can ask; a manager approves store and payee names.";
export const EMPTY_TITLE = 'Ask about the money in {book}';
export const EMPTY_BODY = 'Claude answers from the figures Steward has already worked out: balances, spending totals, cash flow, the plan. Some questions people ask:';
export const EMPTY_BODY_PARENT = "Claude answers from what Steward knows about {name}'s money: the inventory, money coming in, premiums, flags and transactions. Some questions people ask:";
export const EXAMPLES_HOUSEHOLD = ['Are we spending more on dining than last year?', 'Can we cover a year on one income?', 'If I put $500 more a month into the 401(k), when can we retire?', 'What did we spend on the kids this year?'];
export const EXAMPLES_PARENT = ["Is {name}'s supplement plan paid up?", 'What came in from the pension this year?', 'Which accounts still need checking?', 'Is there anything odd this month?'];
export const EXAMPLES_NOTE = 'Tapping one puts it in the box below so you can change it before sending.';
export const BEFORE_TITLE = 'Before you ask';
export const BEFORE_MEMBERS = 'In {book}, the members are {names}. You can read the log at any time in Settings → Chat.';
export const START = 'Start';
export const AWAY_TITLE = 'Chat needs {gateway}';
export const AWAY_BODY = 'Your earlier conversations and the log of what was sent are still here.';
export const AWAY_NOTE = 'Steward checks every minute and turns chat back on by itself.';
export const AWAY_LAST = '{gateway} last answered {when}.';
export const PAUSED_TITLE = 'Chat is paused';
export const CAP_TITLE = "This month's budget is used up";
export const CHANGE_BUDGET = 'Change the budget';
export const OFF_TITLE = 'Chat is off for this book';
export const OFF_BODY = 'A manager can turn it on in Settings → Chat.';
export const ASK_OFF_SAMPLE = 'Ask is off in the Sample.';
export const GO_SETTINGS = 'Go to Settings → Chat';
export const BOX_LABEL = 'Your question';
export const BOX_PLACEHOLDER = 'Ask about this book’s money';
export const BOX_PLACEHOLDER_PARENT = 'Ask about {name}’s money';
export const FOLLOW_UP = 'Ask a follow-up';
export const WAITING_CARD = 'Waiting for your answer above';
export const PAUSED_BOX = 'Paused until {gateway} answers';
export const CAP_BOX = 'Chat starts again on {date}';
export const SEND = 'Send';
export const EDIT = 'Edit';
export const WILL_SEND = 'What Claude will see';
export const AS_WRITTEN = 'Claude will see your question as written.';
export const COMPOSER_NOTE = "As you type, this shows exactly what leaves Steward. What you type is yours to judge: Steward replaces the names and account numbers it knows, not everything you might write.";
export const TAP_NOTE = 'Tap a replaced part to see why.';
export const PREVIEW_FAILED = "What Claude will see can't be shown, so nothing can be sent. {error}";
export const LEGEND_STEWARD = 'Replaced in Steward';
export const LEGEND_MODEL = "Removed by {gateway}'s check";
// Said after a chip's text, for a screen reader: the eye has the chip's icon and colour.
export const CHIP_SAID_STEWARD = ', replaced in Steward';
export const CHIP_SAID_MODEL = ", removed by {gateway}'s check";
export const CONFIRM_ONE = "{gateway}'s check took out 1 part it guessed was a name or a number. Steward's own replacements are always right; the check's guesses can be wrong either way, so this one is shown before sending.";
export const CONFIRM_MANY = "{gateway}'s check took out {n} parts it guessed were names or numbers. Steward's own replacements are always right; the check's guesses can be wrong either way, so these are shown before sending.";
export const POP_PERSON = 'Replaced in Steward. {label} is in this book, so Claude gets "{alias}". Answers show "{label}" again.';
export const POP_ACCOUNT = 'Replaced in Steward. This is {label} in this book, so Claude gets "{alias}". Account names and numbers aren\'t sent.';
export const POP_PAYEE = 'Replaced in Steward. This is {label}, so Claude gets "{alias}". Store and payee names aren\'t sent unless a manager approves them.';
export const POP_STEWARD_BRACKET = "Replaced in Steward. It matched {what} this book knows, so it isn't sent.";
export const POP_STEWARD_SECRET = "Replaced in Steward. It came right after a PIN, password or code, so it isn't sent.";
export const POP_MODEL = "Removed by {gateway}'s check. It looked like {what}.";
export const POP_MODEL_AFTER = "Claude can still answer from the rest. If it isn't needed, nothing changes; if it's wrong, edit the question.";
export const WHAT_STEWARD: Record<string, string> = {
  '[name]': 'a name', '[payee]': 'a store or payee name', '[institution]': 'a bank or company name', '[email]': 'an email address',
  '[phone]': 'a phone number', '[address]': 'an address', '[account number]': 'an account number', '[date of birth]': 'a date of birth',
  '[number]': 'a long number',
};
// What the check took out, filled into POP_MODEL. Each says only that this part isn't sent: the check guesses, and a
// part it misses goes as typed (Privacy, "What the checks can miss").
export const WHAT_MODEL: Record<string, string> = {
  '[organization]': "the name of a business, so it isn't sent",
  '[name]': "a person's name, so it isn't sent. The check looks for names Steward doesn't hold and can miss one",
  '[account number]': "an account number, so it isn't sent", '[card number]': "a card number, so it isn't sent",
  '[ssn]': "a Social Security number, so it isn't sent", '[routing number]': "a routing number, so it isn't sent",
  '[iban]': "a bank account number, so it isn't sent", '[email]': "an email address, so it isn't sent",
  '[phone]': "a phone number, so it isn't sent", '[address]': "a street address, so it isn't sent",
  '[date of birth]': "a date of birth, so it isn't sent", '[secret]': "a PIN, password or code, so it isn't sent",
};
export const SENT_AS_WRITTEN = 'Sent as written';
export const SENT_AS = 'Sent as: ';
export const SENDING = 'Sending your question…';
export const STOP = 'Stop';
export const ASK_AGAIN = 'Ask again';
export const WHAT_SAW = 'What Claude saw ({n})';
export const SHOW_SENT = 'Show what was sent';
export const HIDE_SENT = 'Hide what was sent';
export const ALIAS_NOTE = 'Names with a dotted line were sent as aliases. Tap one to see the alias.';
export const ALIAS_TIP = 'Sent as {alias}';
export const NEW_CONVERSATION = 'New conversation';
export const OPEN_LOOK_INTO = 'Open Look into';
export const SEE_IN_LOG = 'See them in the log';
export const CARD_TITLE = 'Claude asks for store names';
export const CARD_ROWS_ASK = 'Claude asks for these {rows}, from {first} to {last}.';
export const CARD_PAYEES_ASK = 'Claude asks for the names behind these payees.';
export const CARD_NAMES = 'The store names that would be sent';
export const CARD_EVERY_ROW = "Every row as it would go, apart from a person's name your gateway finds";
export const CARD_ACCOUNTS = '{pairs}; Claude gets only the alias. Nothing else from these transactions is sent.';
export const CARD_MORE = 'More rows matched; these are the newest 200.';
export const CARD_SEND_NAME = 'Send this name too';
export const APPROVE = 'Approve for this conversation';
export const DONT_SEND = "Don't send";
export const CARD_FOOT = "The approval is kept in the book's log with your name. Without an answer by {time}, Claude stops and nothing is sent.";
export const VIEWER_CARD_TITLE = 'Claude asked for store and payee names';
export const VIEWER_CARD_BODY = "Store and payee names need a manager's OK. Claude will answer from totals.";
export const VIEWER_CARD_WHO = '{managers} can see the names in {where}, or ask the same question and approve them.';
export const COL_DATE = 'Date';
export const COL_STORE = 'Store';
export const COL_AMOUNT = 'Amount';
export const COL_CATEGORY = 'Category';
export const COL_ACCOUNT = 'Account';
export const HISTORY_TITLE = 'Your conversations';
export const HISTORY_SUB = 'In {book}. Listed only for you.';
export const GROUPS = { today: 'Today', week: 'This week', earlier: 'Earlier' } as const;
export const STORE_NAMES_SENT = 'Store names sent';
export const DELETE = 'Delete';
export const DELETE_TITLE = 'Delete "{title}"?';
export const DELETE_BODY = "It's removed from your conversations. The {requests} it sent stay in the book's log, because the log is never edited.";
export const DELETE_BODY_NONE = "It's removed from your conversations.";
export const DELETED = 'Deleted "{title}".';
export const KEEP = 'Keep';
export const HISTORY_EMPTY = 'No conversations yet. The questions you ask in {book} are listed here, newest first.';
export const HISTORY_FOOT = "Conversations are listed only for the person who asked them. What every conversation sent is in the book's log, which every member can read.";
export const OPEN_LOG = 'Everything sent so far';
export const STOPPED = 'You stopped this answer.';
export const DIDNT_FINISH = "This question didn't finish.";

// Settings → Chat → Sent to Anthropic (design pass screen 4).
export const LOG_LEDE = "Everything sent from {book} to Anthropic, newest first. {names} can read it, and it can't be edited or deleted, even when a conversation is.";
export const WHO = 'Who';
export const EVERYONE = 'Everyone';
export const WHAT = 'What';
export const EVERYTHING = 'Everything';
export const WHAT_OPTIONS: Record<string, string> = { question: 'Questions', results: 'Results', store_rows: 'Store rows', prewarm: 'Getting ready' };
export const DAY_HEAD = '{day} · {n}';
export const BACK_TO_CHAT = 'Settings → Chat';
export const BACK_TO_ASK = 'Ask';
export const D_WHEN = 'When';
export const D_WHO = 'Asked by';
export const D_APPROVED = 'Approved by';
export const D_CONVERSATION = 'Conversation';
export const D_SIZE = 'Size';
export const CHECKS_TITLE = 'Checked on {gateway} before sending';
export const EXACT_TITLE = 'Exactly what was sent';
export const FINGERPRINT = 'Fingerprint {hash}';
export const FIXED_SHOW = "Steward's fixed instructions";
export const FIXED_SAME = "Fingerprint {hash}, the same as this row's.";
export const FIXED_OTHER = 'Fingerprint {hash}. This row was sent with the instructions of another version.';
export const FIXED_AWAY = "{gateway} isn't answering, so the text can't be shown now.";
export const FIXED_TOOLS = 'Tools Claude can call';
export const FIXED_HASHED = 'The exact text hashed';
export const ALIAS_IS = '{alias} is {label}';

// Settings → Chat (design pass screen 7).
export const SETTINGS_LEDE = "Ask Claude about a book's money. Steward sends figures, not the account numbers and names it holds, except store and payee names a manager approves. Your gateway looks for other names and can miss one. Each part sent is logged.";
export const GATEWAY = '{gateway}';
export const GATEWAY_OK = '{gateway} is answering';
export const GATEWAY_AWAY = "{gateway} isn't answering";
// Each check's words when it passed, and when it failed: the tick or cross beside them is hidden from a screen reader.
export const CHECK_REDACTION = ['Privacy check is loaded', "Privacy check isn't loaded"] as const;
export const CHECK_KEY = ['The Claude key works', "The Claude key doesn't work"] as const;
export const CHECK_CATEGORIZER = ['Categorizer is answering', "Categorizer isn't answering"] as const;
export const PDF_ON_GATEWAY = 'PDFs are read on {gateway}';
export const PDF_ON_STEWARD = 'PDFs are read on Steward';
export const GATEWAY_CHECKED = 'Checked {when}. To stop all chat without opening Steward, run {command} on {gateway}.';
export const STOP_COMMAND = 'steward-gateway stop-chat';
export const GATEWAY_AWAY_NOTE = "Last answered {when}. Chat and categorization are paused until it's back; nothing is sent another way.";
export const GATEWAY_NEVER = "Chat and categorization are paused until it answers; nothing is sent another way.";
export const BOOKS_TITLE = 'Chat in each book';
export const BOOKS_NOTE = "Off in every book until a manager turns it on, which shows the disclosure first. Turning it off stops new questions at once; conversations and the log stay. Only books you belong to are listed, and only a book's managers can switch it.";
export const TURN_ON_IN = 'Turn on chat in {book}?';
export const TURN_ON_NOTE = '{names} will each see this once, the first time they open Ask in this book.';
export const TURN_ON_NOTE_ONE = '{names} will see this once, the first time they open Ask in this book.';
export const TURN_ON_VIEWERS = "{viewers} can ask but can't approve store names.";
export const CANCEL = 'Cancel';
export const SENT_ROW = '{book}: {n} this month';
export const SENT_LAST = 'last {when}';
export const SENT_NOTE = 'Every member of a book can read its log, viewers included.';
export const BUDGET_TITLE = 'Monthly budget';
export const METER_LABEL = '{pct}% of the budget used';
export const PACE = 'At this pace {month} ends near {amount}. Chat stops at {cap} and starts again on the 1st.';
export const BUDGET_FIELD = 'Budget a month, in dollars';
export const BUDGET_KEY_NOTE = "From $1 to $100. The Claude key also has a monthly limit set at Anthropic, so spending stops there even if Steward's count were wrong.";
export const COL_THIS_MONTH = 'This month';
export const COL_QUESTIONS = 'Questions';
export const COL_COST = 'Cost';
export const EARLIER_MONTHS = 'Earlier months are listed here as they pass, up to 12.';
export const MODEL_TITLE = 'Answer model';
export const MODEL_INFO: Record<string, { name: string; about: string }> = {
  'claude-opus-5-5': { name: 'Claude Opus 5.5', about: 'Best with plan and several-step questions. About $0.12 a question, so $10 covers about 80.' },
  'claude-sonnet-5-5': { name: 'Claude Sonnet 5.5', about: 'About half the cost. About $0.06 a question, so $10 covers about 165.' },
};
export const MODEL_NOTE = 'The budget and the model apply to every book. Managers of {household} can change them; everyone else sees them.';
export const STREAM_BROKE = "The answer stopped arriving. Check that this device is on your home network or Tailscale, then ask again.";
export const STREAM_GARBLED = "Steward sent part of this answer in a form the page couldn't read, so it stopped. Ask again.";
export const START_FAILED = "Steward couldn't note that you read this. Try Start again.";

// Settings → Chat → Claude Code (spec 7, ruling 3).
export const CC_TITLE = 'Claude Code';
export const CC_NOTE = "Claude Code on {gateway} can look up a book's figures. What each lookup returns is checked as chat's lookups are: Steward replaces the account numbers and names it holds, and {gateway}'s check looks for other names and can miss one. What you type into Claude Code doesn't pass through Steward. A grant is for one person in one book and lasts 30 days. What Claude Code gets is kept in the book's log, and store and payee names are never offered.";
export const CC_NONE = 'No Claude Code grants.';
export const CC_ISSUE = 'Issue a grant';
export const CC_ISSUE_IN = 'Claude Code grant for {book}';
export const CC_FOR_YOU = 'This grant is for you. Each manager issues their own.';
export const CC_ISSUE_NOTE = 'Read-only, for {days} days. You can revoke it here at any time.';
export const CC_ONCE = 'Copy this now. Steward shows it only once.';
export const CC_RUN = 'Run the command in a plain terminal and paste the grant when it asks.';
export const CC_COPY = 'Copy';
export const CC_DONE = 'Done';
export const CC_ROW = '{person} · issued by {by}, {day}';
export const CC_REVOKE = 'Revoke';
export const CC_REVOKE_Q = "Revoke {person}'s grant for {book}?";
export const CC_REVOKE_NOTE = 'Claude Code stops getting answers from {book} at once. Nothing in {book} changes, and a manager can issue a new grant.';
export const CC_REVOKE_FOR = "Revoke {person}'s grant";
export const CC_REVOKED = "Revoked {person}'s grant for {book}.";

// Ask about this (phase 13 spec, Copy). The chip is built from POST /chat/about's wire object.
export const ABOUT_CHIP = 'About: {thing}';
export const ABOUT_SENT_AS = ' · sent as {alias}';
export const ABOUT_REMOVE = 'Remove: About {thing}';
export const ABOUT_REMOVED = '+ About: {thing}';
export const ABOUT_ADD_BACK = 'Add back: About {thing}';
export const ABOUT_GONE = "That isn't in this book any more.";
export const ABOUT_PLAN = 'the Plan';
export const ABOUT_CARE = 'Care';
export const ABOUT_FLAG = '{check} flag';
export const ABOUT_MONEY_OUT = '{amount} out';
export const ABOUT_MONEY_IN = '{amount} in';
/** The watching checks' labels (steward/watch/vocab.py CHECK_LABELS; tests/advisor/test_vocab_parity.py keeps them equal).
 *  "Unusual for {name}" reads "Unusual": the chip names nobody (Engineering choice 5). */
export const CHECK_WORDS: Record<string, string> = { scam: 'Scam pattern', payee: 'New payee', large: 'Large amount', unusual: 'Unusual', cash: 'Cash', monthly: 'New monthly charge', duplicate: 'Duplicate', deposit: 'Missing deposit', premium: 'Missed premium', account: 'New account', low: 'Low balance' };
/** The Care screen's level words (screens/care/copy.ts LEVELS; aboutChip.test.ts keeps them equal). */
export const CARE_LEVEL_WORDS: Record<string, string> = { at_home: 'At home as today', in_home_help: 'In-home help', assisted_living: 'Assisted living', memory_care: 'Memory care', nursing_home: 'Nursing home' };
export const OPEN_IN_ASK = 'Open in Ask';
export const ANSWER_READY = 'Answer ready';
export const OPEN = 'Open';
export const TOUR = 'Names, stores and accounts turn into placeholders before anything leaves Steward.';
export const TOUR_TAP = 'Tap one to see what it stands for.';
export const GOT_IT = 'Got it';
export const TOUR_EXAMPLE = 'Example';
export const TOUR_EXAMPLE_ASKED = 'How much did Sam spend at Costco on our Chase card?';
export const TOUR_EXAMPLE_SENT = 'How much did Person 2 spend at Payee 4 on our Account 2?';
