{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "VARCHAR"
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "GREATER_THAN",
        "operator": ">",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 1,
            "name": "$1",
            "type": "FLOAT"
          },
          {
            "kind": "LITERAL",
            "value": 50,
            "type": "INTEGER",
            "nullable": false,
            "precision": 10
          }
        ]
      },
      "variableset": "[]",
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalAggregate",
          "keys": [
            {
              "column": 0,
              "type": "VARCHAR"
            }
          ],
          "aggs": [
            {
              "operator": "AVG",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 1,
                  "type": "FLOAT"
                }
              ],
              "type": "FLOAT",
              "name": null
            }
          ],
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "FLOAT"
                }
              ],
              "id": "1",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "expense",
                  "id": "0",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}