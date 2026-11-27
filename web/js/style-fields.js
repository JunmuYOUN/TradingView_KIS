import { TOOLS } from './drawings.js';

const properties = {
  color: 'color', width: 'width', dash: 'dash', extend: 'extend',
  'note-text': 'text', glyph: 'glyph', 'font-size': 'fontSize',
};

function valueOf(input) {
  if (input.type === 'checkbox') return input.checked;
  if (input.name === 'width' || input.name === 'fontSize') return Number(input.value);
  return input.value;
}

// Chart and preset settings share the same controls, valid choices and type-specific visibility.
export function mountStyleFields(container, prefix, onChange) {
  const contents = document.getElementById('style-fields-template').content.cloneNode(true);
  const inputs = [...contents.querySelectorAll('input, select')];
  for (const input of inputs) {
    input.name = properties[input.id];
    if (onChange) {
      input.addEventListener(input.type === 'text' ? 'input' : 'change', () => {
        onChange({ [input.name]: valueOf(input) });
      });
    }
  }
  for (const element of contents.querySelectorAll('[id]')) element.id = prefix + element.id;
  container.append(contents);
  return {
    show(type, style) {
      const annotation = type === 'text' || type === 'marker';
      const values = {
        color: TOOLS[type].color, width: 1.5, dash: 'solid', extend: false, text: '메모',
        glyph: '☺', fontSize: type === 'marker' ? 24 : 16, ...style,
      };
      const visible = { color: true, width: !annotation, dash: !annotation, extend: type === 'fib',
        text: type === 'text', glyph: type === 'marker', fontSize: annotation };
      for (const input of inputs) {
        input.closest('label').hidden = !visible[input.name];
        if (input.type === 'checkbox') input.checked = !!values[input.name];
        else input.value = values[input.name];
      }
    },
    read() {
      return Object.fromEntries(inputs.filter(input => !input.closest('label').hidden)
        .map(input => [input.name, valueOf(input)]));
    },
  };
}
