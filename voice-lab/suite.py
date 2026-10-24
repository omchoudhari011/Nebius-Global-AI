"""What people say to Jarvis, for every kind of task, and what each must lead to.

expect:  ("fast", function, {arg: value-or-substring})  an exact command, no model (app/fastpath.py)
         ("skill", name)                                  the model, with this skill
         ("end",)                                         ends the conversation on the PC (jarvis.py)
         ("hud", "show"|"hide")                           the HUD, handled on the PC
"""

EN = [
    # media
    ("play shape of you on spotify", ("fast", "pc_play", {"query": "shape of you", "service": "spotify"})),
    ("pause the music", ("fast", "pc_media", {"action": "play_pause"})),
    ("next song", ("fast", "pc_media", {"action": "next"})),
    ("set the volume to forty", ("fast", "pc_set_volume", {"level": 40})),
    ("turn the volume up", ("fast", "pc_media", {"action": "volume_up"})),
    ("mute", ("fast", "pc_media", {"action": "mute"})),
    ("what's playing right now", ("fast", "pc_now_playing", {})),
    # apps and sites
    ("open chrome", ("fast", "pc_open", {"target": "chrome"})),
    ("open youtube", ("fast", "pc_open", {"target": "youtube"})),
    ("close notepad", ("fast", "pc_close_app", {"name": "notepad"})),
    ("open my downloads folder", ("fast", "pc_open", {"target": "downloads"})),
    ("search amazon for wireless earbuds", ("fast", "pc_search_site", {"site": "amazon", "query": "wireless earbuds"})),
    # system
    ("turn on dark mode", ("fast", "pc_set_theme", {"mode": "dark"})),
    ("dim the screen", ("fast", "pc_set_brightness", {})),
    ("how much battery do I have", ("fast", "pc_system_info", {})),
    ("lock my computer", ("fast", "pc_lock_screen", {})),
    ("set a timer for ten minutes", ("fast", "pc_timer", {"seconds": 600})),
    ("wake me up at seven in the morning", ("fast", "pc_alarm", {"time": "07:00"})),
    ("system status", ("fast", "pc_system_info", {})),
    ("turn on do not disturb", ("fast", "pc_do_not_disturb", {"on": True})),
    # reminders, calendar, notes
    ("remind me to call mum at six pm", ("skill", "reminders")),
    ("what reminders do I have", ("skill", "reminders")),
    ("what's on my calendar tomorrow", ("skill", "calendar")),
    ("add a meeting with Priya on Monday at three", ("skill", "calendar")),
    ("take a note buy milk and eggs", ("skill", "notes")),
    # the web
    ("what's the weather in Pune today", ("skill", "web")),
    ("will it rain tomorrow", ("skill", "web")),
    ("who won the cricket match yesterday", ("skill", "web")),
    ("compare the iPhone sixteen and the Pixel nine", ("skill", "research")),
    # messages and mail
    ("send a whatsapp to Tanishka saying I'll be ten minutes late", ("skill", "pc")),
    ("what did I miss", ("skill", "pc")),
    ("any new emails from the bank", ("skill", "email")),
    # memory
    ("remember that my sister Meera works at Infosys", ("skill", "general")),
    ("what does my sister do", ("skill", "general")),
    # Jarvis itself
    ("show the HUD", ("hud", "show")),
    ("that's all thanks", ("end",)),
    ("undo what you just did", ("fast", "undo_last_action", {})),
    ("what can you do", ("skill", "general")),
]

HI = [  # Hindi, said in Devanagari's sounds; Whisper writes it in Devanagari or Latin
    ("क्रोम खोलो", ("fast", "pc_open", {"target": "chrome"})),
    ("आवाज़ बढ़ाओ", ("fast", "pc_media", {"action": "volume_up"})),
    ("गाना बंद करो", ("fast", "pc_media", {"action": "play_pause"})),
    ("मुझे शाम छह बजे मम्मी को फ़ोन करने की याद दिलाना", ("skill", "reminders")),
    ("पुणे में आज मौसम कैसा है", ("skill", "web")),
    ("दस मिनट का टाइमर लगाओ", ("fast", "pc_timer", {"seconds": 600})),
]

VOICES_EN = ["en_GB-alan-medium", "en_US-lessac-medium", "en_US-amy-medium", "en_GB-jenny_dioco-medium",
             "en_US-ryan-medium", "hi_IN-rohan-medium", "hi_IN-priyamvada-medium"]  # the last two: an Indian accent
VOICES_HI = ["hi_IN-pratham-medium", "hi_IN-rohan-medium", "hi_IN-priyamvada-medium"]
