import os
import re

templates_dir = 'templates'

for filename in os.listdir(templates_dir):
    if filename.endswith('.html'):
        filepath = os.path.join(templates_dir, filename)

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        content = re.sub(r'href="css/([\w.\-/]+)"', "href=\"{{ url_for('static', filename='css/\\1') }}\"", content)
        content = re.sub(r'href="vendor/([\w.\-/]+)"', "href=\"{{ url_for('static', filename='vendor/\\1') }}\"", content)
        content = re.sub(r'src="vendor/([\w.\-/]+)"', "src=\"{{ url_for('static', filename='vendor/\\1') }}\"", content)
        content = re.sub(r'src="js/([\w.\-/]+)"', "src=\"{{ url_for('static', filename='js/\\1') }}\"", content)
        content = re.sub(r'src="img/([\w.\-/]+)"', "src=\"{{ url_for('static', filename='img/\\1') }}\"", content)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f'Fixed: {filename}')

print('All done!')
