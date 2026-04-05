import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

# Load data
df = pd.read_parquet('data.parquet')

# Calculate correlation with MEDV_per_RM
correlation = df[['MEDV', 'RM', 'LSTAT', 'PTRATIO', 'TAX']].corrwith(df['MEDV'])

# Get top 3 columns with highest correlation
top_3_columns = correlation.sort_values(ascending=False).index[:3]

# Plot scatter plots for MEDV_per_RM with top 3 columns
for i, column in enumerate(top_3_columns):
    plt.figure(figsize=(8, 6))
    sns.scatterplot(x=df[column], y=df['MEDV'])
    plt.title(f'MEDV vs {column}')
    plt.xlabel(column)
    plt.ylabel('MEDV')
    
    # Calculate R²
    model = LinearRegression()
    model.fit(df[[column]], df['MEDV'])
    y_pred = model.predict(df[[column]])
    r2 = r2_score(df['MEDV'], y_pred)
    plt.text(0.1, 0.9, f'R² = {r2:.2f}', transform=plt.gca().transAxes)
    
    plt.savefig(f'plot_{i+1}.png')
    plt.close()