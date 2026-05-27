-- Public project metadata description.
-- The projects API reads this column in list/detail metadata responses.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '';
