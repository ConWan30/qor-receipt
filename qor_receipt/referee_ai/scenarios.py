"""Frame-level evaluation set (22 real Madden capture frames + 3 doctored copies) and the referee scenarios
built from them. Used by eval/referee_ai_frames.py (live Jev) and tests/test_referee_ai.py (recorded Jev)."""
from __future__ import annotations

N = None
# file: (frame_type, away, home, quarter, clock_s)
GROUND_TRUTH = {
    "live_q4_0-30_NO7_NYG0.jpg": ("live", 7, 0, 4, 30),
    "live_q4_0-20_NO7_NYG0.jpg": ("live", 7, 0, 4, 20),
    "live_q4_0-00_NO7_NYG8.jpg": ("live", 7, 8, 4, 0),
    "live_q1_2-48_ATL6_CIN0.jpg": ("live", 6, 0, 1, 168),
    "live_banner_occluded_q1_2-30.jpg": ("live", N, N, 1, 150),
    "menu_pause_q4_1-23_NO7_NYG6.jpg": ("menu", 7, 6, 4, 83),
    "menu_pause_q1_3-57_NO0_DAL0.png": ("menu", 0, 0, 1, 237),
    "menu_pause_q3_5-00_ATL14_CIN0.jpg": ("menu", 14, 0, 3, 300),
    "final_menu_NO7_NYG8_a.jpg": ("final", 7, 8, 4, 0),
    "final_menu_NO7_NYG8_b.jpg": ("final", 7, 8, 4, 0),
    "black_obs.png": ("black", N, N, N, N),
    "cutscene_win_celebration.jpg": ("cutscene", N, N, N, N),
    "cutscene_td_banner.jpg": ("cutscene", N, N, N, N),
    "transition_madden_wipe.jpg": ("cutscene", N, N, N, N),
    "cutscene_stat_overlay.jpg": ("cutscene", N, N, N, N),
    "strip_std_29_15_4th_2-38.png": (N, 29, 15, 4, 158),
    "strip_std_14_7_2nd_1-23.png": (N, 14, 7, 2, 83),
    "strip_std_21_24_4th_0-32.png": (N, 21, 24, 4, 32),
    "strip_std_22_7_3rd_4-00.png": (N, 22, 7, 3, 240),
    "TAMPER_score_NYG0to21.jpg": ("live", 7, 21, 4, 30),
    "TAMPER_clock_0-00to4-00.jpg": ("live", 7, 8, 4, 240),
    "TAMPER_clone_NYG8to7.jpg": ("live", 7, 7, 4, 0),
}

# name: (frames in time order, claimed final {away, home} or None, expected verdict)
SCENARIOS = {
    "clean_game_end_7_8": (["live_q4_0-30_NO7_NYG0.jpg", "live_q4_0-20_NO7_NYG0.jpg", "live_q4_0-00_NO7_NYG8.jpg",
                            "final_menu_NO7_NYG8_a.jpg", "final_menu_NO7_NYG8_b.jpg"], {"away": 7, "home": 8}, "accept"),
    "wrong_claim_8_7": (["live_q4_0-30_NO7_NYG0.jpg", "live_q4_0-20_NO7_NYG0.jpg", "live_q4_0-00_NO7_NYG8.jpg",
                         "final_menu_NO7_NYG8_a.jpg", "final_menu_NO7_NYG8_b.jpg"], {"away": 8, "home": 7}, "dispute"),
    "doctored_score_7_21": (["live_q4_0-30_NO7_NYG0.jpg", "live_q4_0-20_NO7_NYG0.jpg", "TAMPER_score_NYG0to21.jpg"],
                            {"away": 7, "home": 21}, "dispute"),
    "doctored_clock_4_00": (["live_q4_0-30_NO7_NYG0.jpg", "live_q4_0-20_NO7_NYG0.jpg", "TAMPER_clock_0-00to4-00.jpg"],
                            {"away": 7, "home": 8}, "dispute"),
    "doctored_clone_7_7": (["live_q4_0-30_NO7_NYG0.jpg", "live_q4_0-20_NO7_NYG0.jpg", "TAMPER_clone_NYG8to7.jpg",
                            "final_menu_NO7_NYG8_a.jpg", "final_menu_NO7_NYG8_b.jpg"], {"away": 7, "home": 7}, "dispute"),
    "no_evidence_black_cutscenes": (["black_obs.png", "transition_madden_wipe.jpg", "cutscene_win_celebration.jpg"],
                                    {"away": 7, "home": 8}, "abstain"),
    "occluded_scorebug_only": (["live_banner_occluded_q1_2-30.jpg", "cutscene_td_banner.jpg"], {"away": 0, "home": 0}, "abstain"),
}
