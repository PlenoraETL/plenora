-- Titolo personalizzato della catena della home (09/11/2026).
--
-- La home mostra i progetti come catene; il titolo calcolato e' il nome del
-- nodo finale con il cammino piu' lungo (frontend/src/utils/projectChains.js).
-- Chi puo' modificare i metadati del progetto puo' rinominare la catena: il
-- titolo si salva sul progetto piu' a valle della catena (PATCH
-- /projects/{id}, campo chain_title). NULL = titolo calcolato.
--
-- Colonna nuova: i metadati dei progetti sono colonne (name, description,
-- tags jsonb lista di stringhe); nessuna colonna JSON libera li ospita.
--
-- SICUREZZA DELL'INSTALLAZIONE: solo ADD COLUMN IF NOT EXISTS, nullable,
-- senza default da riscrivere: nessun dato esistente viene letto o
-- modificato, si puo' rieseguire.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS chain_title TEXT;
