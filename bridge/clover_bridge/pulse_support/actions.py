"""Pulse disturbance-event dispatch — the leaf shared by two callers:

  * ``simulate.py``    (closed-loop MPC evaluation), and
  * the reservoir open-loop data collector (``reservoir/datagen/collect.py``).

It deliberately imports **nothing** from Pulse, ``common``, or ``mpc_controller`` at module
load, so a system-identification data-gen process can reuse the exact same disturbance
semantics without dragging in the controller stack. ``build_action_map()`` performs the
Pulse imports lazily and MUST be called after ``pulse_env.bootstrap()``.

Param names match the pulse_gui converter output. Naming follows the GUI labels:
``compound_infusion`` = a single drug (SESubstanceInfusion); ``fluid_infusion`` = fluid/blood
(SESubstanceCompoundInfusion); ``metabolic_load`` = the VCO2 knob (SEExercise).
"""
from __future__ import annotations

# Pulse bleed sites the GUI/server expose (eHemorrhage_Compartment members).
HEMO_COMPARTMENTS = ("RightLeg", "LeftLeg", "RightArm", "LeftArm", "Aorta", "VenaCava")

# Acute single-scalar-severity actions: event type -> action-class key in the `m` dict.
# (pulmonary_shunt uses SEPulmonaryShuntExacerbation.get_severity(), verified working.)
SIMPLE_SEVERITY = {"airway_obstruction": "AirwayObstruction", "acute_stress": "AcuteStress",
                   "asthma_attack": "Asthma", "pulmonary_shunt": "Shunt"}

# Infusion event types that latch on until rate 0 (a finite duration_s schedules an auto-stop).
INFUSION_TYPES = ("compound_infusion", "fluid_infusion", "infusion")


class Synth:
    """Minimal stand-in for an Event (type + params), for synthetic auto-stop actions."""
    __slots__ = ("type", "params")
    def __init__(self, type, params):
        self.type, self.params = type, params


def build_action_map() -> dict:
    """Construct the action-class + unit/enum dispatch map ``m``.

    Lazily imports Pulse, so this must run after ``pulse_env.bootstrap()``. The keys are the
    short names ``apply_event`` (and simulate's ``_apply_vc``) index into.
    """
    from pulse.cdm.patient_actions import (
        SEIntubation, eIntubationType, SESubstanceInfusion, SESubstanceCompoundInfusion,
        SESubstanceBolus, eSubstance_Administration,
        SEAcuteRespiratoryDistressSyndromeExacerbation, SEHemorrhage, eHemorrhage_Compartment,
        SEAirwayObstruction, SEAcuteStress, SEExercise, SEDyspnea, SEAsthmaAttack,
        SEBrainInjury, eBrainInjuryType, SEImpairedAlveolarExchangeExacerbation,
        SEPulmonaryShuntExacerbation, SETensionPneumothorax, SENeedleDecompression)
    from pulse.cdm.physiology import eLungCompartment
    from pulse.cdm.engine import eSide, eGate
    from pulse.cdm.mechanical_ventilator_actions import (
        SEMechanicalVentilatorVolumeControl, SEMechanicalVentilatorHold)
    from pulse.cdm.mechanical_ventilator import eSwitch
    from pulse.cdm.scalars import (VolumeUnit, FrequencyUnit, PressureUnit, TimeUnit,
                                   VolumePerTimeUnit, MassPerVolumeUnit)
    return {
        "VC": SEMechanicalVentilatorVolumeControl, "On": eSwitch.On, "Off": eSwitch.Off,
        "ARDS": SEAcuteRespiratoryDistressSyndromeExacerbation, "eLung": eLungCompartment,
        "Infusion": SESubstanceInfusion, "Compound": SESubstanceCompoundInfusion,
        "Hemorrhage": SEHemorrhage, "eHemo": eHemorrhage_Compartment,
        "Hold": SEMechanicalVentilatorHold,
        "AirwayObstruction": SEAirwayObstruction, "AcuteStress": SEAcuteStress,
        "Exercise": SEExercise, "Intub": SEIntubation, "eIntub": eIntubationType,
        "Bolus": SESubstanceBolus, "eAdmin": eSubstance_Administration, "Dyspnea": SEDyspnea,
        "Asthma": SEAsthmaAttack,
        # --- trauma/burn additions (all verified present + physiologically active on disk) ---
        "BrainInjury": SEBrainInjury, "eBrainInjury": eBrainInjuryType,
        "ImpairedExchange": SEImpairedAlveolarExchangeExacerbation,
        "Shunt": SEPulmonaryShuntExacerbation,
        "TensionPneumo": SETensionPneumothorax, "Needle": SENeedleDecompression,
        "eSide": eSide, "eGate": eGate,
        # --- unit classes ---
        "TimeUnit": TimeUnit, "VolumeUnit": VolumeUnit, "FrequencyUnit": FrequencyUnit,
        "PressureUnit": PressureUnit, "VolumePerTimeUnit": VolumePerTimeUnit,
        "MassPerVolumeUnit": MassPerVolumeUnit,
    }


def apply_event(pulse, m, ev):
    """Apply one timed disturbance to the running engine. ``ev`` has ``.type`` and ``.params``."""
    t, p = ev.type, ev.params
    if t == "infusion":                 # legacy key for the single-drug infusion
        t = "compound_infusion"
    if t == "metabolic_load":           # VCO2 / hypermetabolism knob == Pulse exercise
        t = "exercise"
    if t == "ards":
        a = m["ARDS"]()
        sev = float(p.get("severity", 0.5))
        # Per-lung: ``left``/``right`` override the shared severity (asymmetric contusion). Absent
        # -> both lungs get ``severity``, so existing {"severity": x} scenarios are unchanged.
        a.get_severity(m["eLung"].LeftLung).set_value(float(p.get("left", sev)))
        a.get_severity(m["eLung"].RightLung).set_value(float(p.get("right", sev)))
        pulse.process_action(a)
    elif t in SIMPLE_SEVERITY:          # single-severity 0-1 acute actions (obstruction/stress/asthma/shunt)
        a = m[SIMPLE_SEVERITY[t]]()
        a.get_severity().set_value(float(p.get("severity", 0.5)))
        pulse.process_action(a)
    elif t == "hemorrhage":
        # rate_mL_per_min == 0 stops an active bleed on that compartment.
        a = m["Hemorrhage"]()
        comp = p.get("compartment", "RightLeg")
        a.set_compartment(getattr(m["eHemo"], comp if comp in HEMO_COMPARTMENTS else "RightLeg"))
        a.get_flow_rate().set_value(float(p.get("rate_mL_per_min", 100.0)),
                                    m["VolumePerTimeUnit"].mL_Per_min)
        pulse.process_action(a)
    elif t == "exercise":               # the only VCO2/metabolic knob (see metabolic_load alias)
        a = m["Exercise"]()
        a.get_intensity().set_value(float(p.get("intensity", 0.5)))
        pulse.process_action(a)
    elif t == "brain_injury":           # TBI: injury_type Diffuse/LeftFocal/RightFocal + 0-1 severity
        a = m["BrainInjury"]()
        a.set_injury_type(getattr(m["eBrainInjury"], p.get("injury_type", "Diffuse"),
                                  m["eBrainInjury"].Diffuse))
        a.get_severity().set_value(float(p.get("severity", 0.5)))
        pulse.process_action(a)
    elif t == "impaired_alveolar_exchange":
        # NB: get_severity() is broken in this Pulse build (serializes as ScalarArea -> crash);
        # impaired_fraction (0-1 of alveolar surface impaired) is the working knob.
        a = m["ImpairedExchange"]()
        a.get_impaired_fraction().set_value(float(p.get("fraction", p.get("severity", 0.5))))
        pulse.process_action(a)
    elif t == "tension_pneumothorax":   # gate Open(tension)/Closed; side Left/Right; 0-1 severity
        a = m["TensionPneumo"]()
        a.set_type(getattr(m["eGate"], p.get("gate", "Open"), m["eGate"].Open))
        a.set_side(getattr(m["eSide"], p.get("side", "Left"), m["eSide"].Left))
        a.get_severity().set_value(float(p.get("severity", 0.5)))
        pulse.process_action(a)
    elif t == "needle_decompression":   # relieve a pneumothorax on a side; state on/off
        a = m["Needle"]()
        a.set_side(getattr(m["eSide"], p.get("side", "Left"), m["eSide"].Left))
        a.set_state(m["On"] if str(p.get("state", "on")).lower() == "on" else m["Off"])
        pulse.process_action(a)
    elif t == "compound_infusion":      # single drug -> Pulse SESubstanceInfusion
        inf = m["Infusion"]()
        inf.set_substance(p.get("drug", "Propofol"))
        inf.get_rate().set_value(float(p.get("rate_mL_per_min", 1.0)), m["VolumePerTimeUnit"].mL_Per_min)
        inf.get_concentration().set_value(float(p.get("conc_g_L", 10.0)),
                                          m["MassPerVolumeUnit"].from_string("g/L"))
        pulse.process_action(inf)
    elif t == "fluid_infusion":         # fluid/blood resuscitation -> Pulse SESubstanceCompoundInfusion
        inf = m["Compound"]()
        inf.set_compound(p.get("compound", "Saline"))
        inf.get_rate().set_value(float(p.get("rate_mL_per_min", 100.0)), m["VolumePerTimeUnit"].mL_Per_min)
        inf.get_bag_volume().set_value(float(p.get("bag_volume_mL", 1000.0)), m["VolumeUnit"].mL)
        pulse.process_action(inf)
    elif t == "vent_hold":              # apnea: state "on" pauses ventilation, "off" resumes
        h = m["Hold"]()
        h.set_state(m["On"] if str(p.get("state", "on")).lower() == "on" else m["Off"])
        pulse.process_action(h)
    elif t == "intubation":             # change tube placement: Esophageal/Mainstem = misplacement; Off = extubate
        a = m["Intub"]()
        typ = p.get("type", "Tracheal")
        a.set_type(getattr(m["eIntub"], typ, m["eIntub"].Tracheal))
        pulse.process_action(a)
    elif t == "dyspnea":                # reduced ventilatory drive: severities 0-1 on RR and VT
        a = m["Dyspnea"]()
        a.get_respiration_rate_severity().set_value(float(p.get("rr_severity", 0.5)))
        a.get_tidal_volume_severity().set_value(float(p.get("vt_severity", 0.5)))
        pulse.process_action(a)
    elif t == "bolus":                  # discrete drug push (SESubstanceBolus): dose(mg)/conc -> volume
        b = m["Bolus"]()
        b.set_substance(p.get("drug", "Propofol"))
        b.set_admin_route(getattr(m["eAdmin"], p.get("route", "Intravenous"), m["eAdmin"].Intravenous))
        conc_g_L = float(p.get("conc_g_L", 10.0))            # g/L == mg/mL
        total_mg = float(p.get("dose", 0.0))                 # already resolved to absolute mg (per-kg handled upstream)
        b.get_dose().set_value(total_mg / conc_g_L if conc_g_L > 0 else 0.0, m["VolumeUnit"].mL)
        b.get_concentration().set_value(conc_g_L, m["MassPerVolumeUnit"].from_string("g/L"))
        dur = float(p.get("admin_duration_s", 0) or 0)
        if dur > 0:
            b.get_admin_duration().set_value(dur, m["TimeUnit"].s)
        pulse.process_action(b)
    else:
        raise ValueError(f"unknown event type: {t!r}")
