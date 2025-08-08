{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "INTEGER"
    },
    {
      "column": 1,
      "type": "VARCHAR"
    },
    {
      "column": 2,
      "type": "DATE"
    }
  ],
  "aggs": [],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "INTEGER"
        },
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 2,
          "name": "$2",
          "type": "DATE"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "LESS_THAN",
            "operator": "<",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 13,
                "name": "$13",
                "type": "FLOAT"
              },
              {
                "kind": "LITERAL",
                "value": 6.0,
                "type": "DECIMAL",
                "nullable": false,
                "precision": 2
              }
            ]
          },
          "variableset": "[]",
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 0,
                    "name": "$0",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 7,
                    "name": "$7",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Patient",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Laboratory",
                  "id": "1",
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