// Default props for the Studio (`npm run studio`) – a short tour through several building blocks.
import type { Storyboard } from "./types";

export const sample: Storyboard = {
  version: 1,
  title: "Baukasten-Rundgang",
  category: "C",
  tone: "cineastisch",
  palette: "ink",
  facts: [{ id: "price_from", value: "499 €", label: "Festpreis für einen Onepager. Einmalig, kein Abo." }],
  scenes: [
    { id: "s1", component: "kinetic_type", duration: 1.6, transition: "cut", props: { lines: [{ text: "Websites," }, { text: "gebaut.", accent: false }, { text: "Nicht geklickt.", accent: true }], style: "slam" } },
    { id: "s2", component: "error_dialog", duration: 1.8, transition: "glitch", props: { severity: "critical", title: "ERROR 404", message: "Kundenvertrauen nicht gefunden.", buttons: ["Ignorieren", "Beheben"] } },
    { id: "s3", component: "split_screen", duration: 2.2, transition: "wipe", props: { left_label: "Baukasten.", right_label: "Echter Code.", left_caption: "2011", right_caption: "heute" } },
    { id: "s4", component: "fact", duration: 1.6, transition: "zoom", surface: "accent", props: { fact_id: "price_from", kicker: "Festpreis" } },
    { id: "s5", component: "end_card", duration: 2.4, transition: "iris", props: { headline: "Lass uns bauen.", button: "Projekt anfragen", url: "pje-systems.de" } },
  ],
};
