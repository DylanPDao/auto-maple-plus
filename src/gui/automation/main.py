import tkinter as tk
from src.gui.interfaces import Tab, LabelFrame
from src.common.interfaces import Configurable

class Automation(Tab):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, 'Automation', **kwargs)

        self.automationSettings = Automation_Settings(self)
        self.automationSettings.pack(side="top", fill="both", padx=5,pady=5)


class Automation_Settings(LabelFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, 'Triggered Actions', **kwargs)

        #Initialise variable
        self.automation_setting_root = AutomationParams('Automation Settings')
        self.revive_when_dead_toggle = tk.BooleanVar(value=self.automation_setting_root.get("revive_when_dead_toggle"))
        self.auto_cc_toggle = tk.BooleanVar(value=self.automation_setting_root.get("auto_cc_toggle"))
        self.auto_pause_in_town_toggle = tk.BooleanVar(value=self.automation_setting_root.get("auto_pause_in_town_toggle"))
        self.auto_cancel_rune_toggle = tk.BooleanVar(value=self.automation_setting_root.get("auto_cancel_rune_toggle"))

        self.grid_columnconfigure(2, weight=1)
        
        fnlist = {"revive_when_dead":{"toggle": None,"label":"Revive when dead", "params":None, "variables":{"togglestate":self.revive_when_dead_toggle}},
                  "auto_pause_in_town":{"toggle": None,"label":"Auto pause in town (Beta)", "params":None, "variables":{"togglestate":self.auto_pause_in_town_toggle}},
                  "auto_cc":{"toggle": None,"label":"Auto-cc (WIP)", "params":None, "variables":{"togglestate":self.auto_cc_toggle}},
                  "auto_cancel_rune":{"toggle": None,"label":"Auto cancel rune (WIP)", "params":None, "variables":{"togglestate":self.auto_cancel_rune_toggle}}
        }

        for item in fnlist:
            fnlist[item]["toggle"] = tk.Checkbutton(
                                        self,
                                        variable=fnlist[item]["variables"]["togglestate"],
                                        text=fnlist[item]["label"],
                                        command=self._on_change
                                    ).grid(column =0, columnspan=2, padx=5,pady=5, sticky="w")
            if fnlist[item]["params"] != None:
                for parameter in fnlist[item]["params"]:
                    labeltext = tk.Label(self, text=(fnlist[item]["params"][parameter]["paramLabel"]+":"))
                    labeltext.grid(column = 0, padx=5,pady=5, sticky="e")
                    labelrow = labeltext.grid_info()['row']
                    fnlist[item]["params"][parameter]["paramobj"] = tk.Entry(self, textvariable=fnlist[item]["params"][parameter]["var"]).grid(column=1,row=labelrow, padx=5,pady=5)
    def _on_change(self):
        self.automation_setting_root.set('revive_when_dead_toggle',self.revive_when_dead_toggle.get())
        self.automation_setting_root.set('auto_cc_toggle',self.auto_cc_toggle.get())
        self.automation_setting_root.set('auto_pause_in_town_toggle',self.auto_pause_in_town_toggle.get())
        self.automation_setting_root.set('auto_cancel_rune_toggle',self.auto_cancel_rune_toggle.get())
        self.automation_setting_root.save_config()

class AutomationParams(Configurable):
    DEFAULT_CONFIG = {
        'revive_when_dead_toggle': False,
        'auto_cc_toggle': False,
        'auto_pause_in_town_toggle': False,
        'auto_cancel_rune_toggle' : False
    }