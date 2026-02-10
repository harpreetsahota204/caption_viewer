"""
Caption Viewer Operator(s)

| Copyright 2017-2025, Voxel51, Inc.
| `voxel51.com <https://voxel51.com/>`_
|
"""
import re
import json
from html.parser import HTMLParser
import fiftyone as fo
import fiftyone.operators as foo
import fiftyone.operators.types as types
import fiftyone.core.view as fov


class HTMLTableToMarkdown(HTMLParser):
    """Convert HTML tables to markdown tables"""
    
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.rows = []
        self.current_row = []
        self.current_cell = ""
    
    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
            self.rows = []
        elif tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag in ["th", "td"]:
            self.in_cell = True
            self.current_cell = ""
    
    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "tr":
            self.in_row = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag in ["th", "td"]:
            self.in_cell = False
            self.current_row.append(self.current_cell.strip())
    
    def handle_data(self, data):
        if self.in_cell:
            self.current_cell += data
    
    def get_markdown(self):
        if not self.rows:
            return ""
        
        lines = []
        for i, row in enumerate(self.rows):
            # Escape pipe characters in cell content
            escaped_row = [cell.replace("|", "\\|") for cell in row]
            lines.append("| " + " | ".join(escaped_row) + " |")
            # Add separator after first row
            if i == 0:
                lines.append("| " + " | ".join(["---"] * len(row)) + " |")
        
        return "\n".join(lines)


class CaptionViewerPanel(foo.Panel):
    @property
    def config(self):
        return foo.PanelConfig(
            name="caption_viewer_panel",
            label="Caption Viewer",
            surfaces="modal",
            allow_multiple=True,
        )
    
    def _sanitize_content(self, text):
        """Remove potentially dangerous content"""
        if not text:
            return ""
        
        # Remove script tags
        text = re.sub(
            r'<script[^>]*>.*?</script>',
            '',
            text,
            flags=re.IGNORECASE | re.DOTALL
        )
        
        # Remove event handlers
        text = re.sub(
            r'\s*on\w+\s*=\s*["\'][^"\']*["\']',
            '',
            text,
            flags=re.IGNORECASE
        )
        
        return text
    
    def _convert_html_tables_to_markdown(self, text):
        """Convert all HTML tables to markdown"""
        def replace_table(match):
            html_table = match.group(0)
            try:
                parser = HTMLTableToMarkdown()
                parser.feed(html_table)
                markdown_table = parser.get_markdown()
                if markdown_table:
                    return f"\n\n{markdown_table}\n\n"
                return html_table
            except Exception:
                return f"\n```html\n{html_table}\n```\n"
        
        text = re.sub(
            r'<table[\s\S]*?</table>',
            replace_table,
            text,
            flags=re.IGNORECASE
        )
        
        return text
    
    def _detect_and_format_json(self, text):
        """Try to parse and pretty-print JSON"""
        try:
            data = json.loads(text)
            return f"```json\n{json.dumps(data, indent=2)}\n```", True
        except:
            return text, False
    
    def _process_vlm_output(self, text):
        """Main processing pipeline"""
        if not text:
            return ""
        
        # Sanitize
        text = self._sanitize_content(text)
        
        # Try JSON
        processed_text, is_json = self._detect_and_format_json(text)
        if is_json:
            return processed_text
        
        # Convert HTML tables
        text = self._convert_html_tables_to_markdown(text)
        
        # Process escape sequences (convert literal \n to actual newlines)
        text = self._process_escape_sequences(text)
        
        return text
    
    def _process_escape_sequences(self, text):
        """Convert literal escape sequences to actual characters"""
        if not text:
            return ""
        
        # Don't process escape sequences if the text is already in a code block (JSON)
        if text.strip().startswith('```'):
            return text
        
        # Handle literal escape sequences (the string "\n" not actual newline)
        # This handles cases where VLMs output literal "\n" in their text
        if '\\n' in text:
            text = text.replace('\\n', '\n')
        if '\\t' in text:
            text = text.replace('\\t', '\t')
        if '\\r' in text:
            text = text.replace('\\r', '\r')
        
        # Text now has actual newlines (either from above or already present)
        # The code block will preserve these
        return text

    def on_load(self, ctx):
        selected_field = ctx.panel.state.selected_field
        
        if not selected_field:
            ctx.panel.state.empty_state = "No field selected"
            return
        
        sample_id = ctx.current_sample
        if not sample_id:
            return
        
        try:
            # Create a simple view for just this one sample
            view = fov.make_optimized_select_view(ctx.view, [sample_id])
            sample = ctx.view[sample_id]
            
            # Check if field exists before accessing
            if sample.has_field(selected_field):
                field_value = sample[selected_field]
                # Handle None values explicitly
                if field_value is None:
                    ctx.panel.state.display_text = ""
                else:
                    ctx.panel.state.display_text = str(field_value)
            else:
                ctx.panel.state.display_text = ""
                
        except Exception as e:
            # Log error but don't crash
            print(f"Error loading field value: {e}")
            ctx.panel.state.display_text = ""

    def render(self, ctx):
        # Get the valid string-type fields for the current dataset
        valid_fields = _get_string_fields(ctx)

        if not ctx.panel.state.selected_field:
            empty_state = types.Object()
            empty_state.enum(
                "field_selector",
                valid_fields,
                label="Select a field to display",
                on_change=self.on_field_select,
            )
            return types.Property(
                empty_state,
                view=types.GridView(
                    align_x="center",
                    align_y="center",
                    orientation="vertical",
                    height=100,
                ),
            )
        
        panel = types.Object()

        if len(valid_fields):
            selected_field = ctx.panel.state.selected_field
            
            # Create menu with field selector
            menu = panel.menu("menu", overlay="top-right")
            menu.enum(
                "field_selector",
                valid_fields,
                label="Select a field",
                default=selected_field,
                on_change=self.on_field_select,
            )
            
            # Get display text and edit mode state
            display_text = ctx.panel.state.get("display_text", "")
            edit_mode = ctx.panel.state.get("edit_mode", False)

            if edit_mode:
                # --- Edit Mode ---
                # No on_change — let the framework sync the input value
                # to ctx.panel.state.edit_text automatically, so re-renders
                # don't fight the user's keystrokes.
                panel.str(
                    "edit_text",
                    label=f"Editing: {selected_field}",
                    default=ctx.panel.state.get("edit_text", display_text),
                )
                panel.btn(
                    "save_btn",
                    label="Save",
                    on_click=self.on_save_edit,
                    variant="contained",
                )
                panel.btn(
                    "cancel_btn",
                    label="Cancel",
                    on_click=self.on_cancel_edit,
                )
            elif display_text:
                # --- View Mode ---
                # Process VLM output
                processed_text = self._process_vlm_output(display_text)
                
                # Check if already in code block
                is_code_block = processed_text.strip().startswith('```')
                
                if is_code_block:
                    # Already formatted as JSON, render as-is
                    panel.md(f"\n\n{processed_text}")
                else:
                    # Convert newlines to markdown line breaks for ALL content
                    # (including mixed text + tables)
                    processed_text = processed_text.replace('\n', '  \n')
                    panel.md(f"\n\n{processed_text}")
                
                # Edit button
                panel.btn(
                    "edit_btn",
                    label="Edit Caption",
                    on_click=self.on_edit_click,
                )
                
                # Add character count metadata
                panel.str(
                    "char_count",
                    default=f"{len(display_text)} characters",
                    view=types.LabelValueView(read_only=True)
                )
            else:
                # No data in field
                panel.view("no_data", types.Notice(
                    label="No data",
                    description="This sample has no text in the selected field"
                ))
                # Allow adding a caption even when the field is empty
                panel.btn(
                    "edit_btn",
                    label="Add Caption",
                    on_click=self.on_edit_click,
                )

        return types.Property(
            panel, view=types.GridView(height=100, width=100)
        )

    def on_field_select(self, ctx):
        field_name = ctx.params.get("value")
        ctx.panel.state.selected_field = field_name
        ctx.panel.set_title(f"Caption Viewer: {field_name}")
        ctx.panel.state.edit_mode = False
        ctx.panel.state.edit_text = None

        self.on_load(ctx)

    def on_change_current_sample(self, ctx):
        ctx.panel.state.edit_mode = False
        ctx.panel.state.edit_text = None
        self.on_load(ctx)

    def on_edit_click(self, ctx):
        """Enter edit mode with the current display text"""
        display_text = ctx.panel.state.get("display_text", "")
        ctx.panel.state.edit_mode = True
        ctx.panel.state.edit_text = display_text

    def on_save_edit(self, ctx):
        """Save the edited caption back to the sample"""
        sample_id = ctx.current_sample
        selected_field = ctx.panel.state.selected_field
        edited_text = ctx.panel.state.get("edit_text", "")

        if not sample_id or not selected_field:
            return

        try:
            sample = ctx.dataset[sample_id]
            sample[selected_field] = edited_text
            sample.save()

            ctx.panel.state.display_text = edited_text
            ctx.panel.state.edit_mode = False
            ctx.panel.state.edit_text = None
        except Exception as e:
            print(f"Error saving field value: {e}")

    def on_cancel_edit(self, ctx):
        """Discard edits and return to view mode"""
        ctx.panel.state.edit_mode = False
        ctx.panel.state.edit_text = None


def _get_string_fields(ctx):
    """
    Utility to return a list of string fields from the dataset.
    """
    dataset = ctx.view._dataset  # or ctx.view.dataset
    schema = dataset.get_field_schema(flat=True)  # top-level fields

    # Filter for only string-type fields
    str_fields = [
        name for name, field in schema.items()
        if isinstance(field, fo.StringField)
    ]

    return str_fields


def register(p):
    p.register(CaptionViewerPanel)