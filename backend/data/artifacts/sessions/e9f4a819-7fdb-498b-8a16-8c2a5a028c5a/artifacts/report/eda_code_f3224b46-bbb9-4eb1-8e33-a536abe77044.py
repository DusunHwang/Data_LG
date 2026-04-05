import matplotlib.pyplot as plt

# Assuming 'y_test' and 'y_pred' are already defined
plt.scatter(y_test, y_pred)
plt.xlabel('Actual Values (MEDV)')
plt.ylabel('Predicted Values (MEDV)')
plt.title('Actual vs Predicted Values Scatter Plot')
plt.savefig('plot_1.png')
plt.close()