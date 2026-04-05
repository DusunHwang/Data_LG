import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import r2_score

# Load data
data = pd.read_parquet('data.parquet')

# 1. Variables correlation analysis (heatmap)
plt.figure(figsize=(12, 10))
sns.heatmap(data[['CRIM', 'ZN', 'INDUS', 'CHAS', 'NOX', 'RM', 'AGE', 'DIS', 'RAD', 'TAX', 'PTRATIO', 'B', 'LSTAT', 'MEDV', 'CAT.MEDV']].corr(), annot=True, fmt=".2f", cmap='coolwarm', cbar=True)
plt.title('Correlation Heatmap')
plt.savefig('plot_1.png')
plt.close()

# 2. Relationship between target variable and other variables (pairplot)
sns.pairplot(data[['MEDV', 'RM', 'LSTAT', 'DIS', 'TAX']])
plt.suptitle('Pairplot of Target Variable and Other Variables')
plt.savefig('plot_2.png')
plt.close()

# 3. Distribution analysis of target variable (histogram)
plt.figure(figsize=(8, 6))
sns.histplot(data['MEDV'], kde=True)
plt.title('Distribution of Target Variable (MEDV)')
plt.xlabel('MEDV')
plt.ylabel('Frequency')
plt.savefig('plot_3.png')
plt.close()

# 4. Detailed correlation analysis (heatmap)
plt.figure(figsize=(10, 8))
sns.heatmap(data[['RM', 'LSTAT', 'DIS', 'TAX', 'MEDV']].corr(), annot=True, fmt=".2f", cmap='coolwarm', cbar=True)
plt.title('Detailed Correlation Heatmap')
plt.savefig('plot_4.png')
plt.close()

# 5. Relationship between target variable and key variables (scatter plots with R²)
fig, axes = plt.subplots(2, 2, figsize=(12, 10))
variables = ['RM', 'LSTAT', 'DIS', 'TAX']
for i, var in enumerate(variables):
    ax = axes[i // 2, i % 2]
    sns.scatterplot(x=data[var], y=data['MEDV'], ax=ax)
    slope, intercept = np.polyfit(data[var], data['MEDV'], 1)
    ax.plot(data[var], slope * data[var] + intercept, color='red')
    r2 = r2_score(data['MEDV'], slope * data[var] + intercept)
    ax.set_title(f'{var} vs MEDV (R² = {r2:.2f})')
    ax.set_xlabel(var)
    ax.set_ylabel('MEDV')

plt.tight_layout()
plt.savefig('plot_5.png')
plt.close()