import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# Load data
data = pd.read_parquet('data.parquet')

# Plot KDE for 'MperR' column
plt.figure(figsize=(8, 6))
sns.kdeplot(data['MperR'], shade=True)
plt.title('Distribution of MperR')
plt.xlabel('MperR')
plt.ylabel('Density')
plt.savefig('plot_1.png')
plt.close()