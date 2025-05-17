{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 6,
      "name": "$6",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 8,
      "name": "$8",
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
            "kind": "MINUS",
            "operator": "-",
            "type": "FLOAT",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 67,
                "name": "$67",
                "type": "FLOAT"
              },
              {
                "kind": "INPUT_REF",
                "index": 72,
                "name": "$72",
                "type": "FLOAT"
              }
            ]
          },
          {
            "kind": "LITERAL",
            "value": 30,
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
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 49,
                "name": "$49",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "schools",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "frpm",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}